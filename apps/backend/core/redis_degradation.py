# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Shared tracking for the request-path controls that fail open on Redis.

`core.ratelimit` and `core.login_throttle` each catch `RedisError` /
`RuntimeError` and fail open rather than fail the request (see their own
module docstrings for why a shared cache is not a hard dependency of either
control). Both call `record()` here instead of logging directly, which does
two things neither of them logging on its own could do:

  - Dedupes the WARNING itself (#419). Left as a plain `log.warning()` per
    call, sustained traffic to an unauthenticated endpoint during a Redis
    outage turns into one JSON line per request for the whole outage.
    `record()` logs at most once per `_LOG_INTERVAL_SECONDS` per
    (component, action) pair, and folds how many calls were suppressed
    since the last line into the next one rather than dropping that count
    silently.
  - Tracks state one log line cannot answer later (#420): is this still
    happening, how many times has it happened, when did it last happen.
    `snapshot()` is what `/health/ready` reads to surface a PERSISTENT
    degradation even on a check that happens to land between failures -
    the case #420 is actually about, a misconfigured `REDIS_URL` (wrong
    password, an ACL mistake) that `AuthenticationError` reports identically
    to a transient outage everywhere the exception type alone is looked at.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: This process's own pid, read once (it cannot change during the process's
#: life). Carried in `snapshot()` so a caller polling `/health/ready` behind
#: a multi-worker uvicorn (`UVICORN_WORKERS`, several OS processes, each with
#: its OWN copy of the module-level state below) can tell "this specific
#: worker has never seen a degradation" apart from "no worker anywhere has" -
#: the two currently look identical from one HTTP response, since state is
#: not aggregated across workers. See the module docstring's "Known
#: limitation" section.
_PID = os.getpid()

#: One WARNING per (component, action) at most this often. 60s keeps a
#: sustained outage from flooding logs while still giving an operator a
#: line to grep for well inside any reasonable alerting window.
_LOG_INTERVAL_SECONDS = 60.0


@dataclass
class _ActionState:
    count: int = 0
    last_degraded_at: float = 0.0
    last_logged_at: float = 0.0
    suppressed_since_last_log: int = 0


#: Not load-bearing against a race today - every caller of `record()` runs
#: inline in an async request handler on this process's one event-loop
#: thread, never offloaded to a thread pool, so nothing can call `record()`
#: concurrently with itself under the current call graph (security review
#: confirmed by tracing both call chains). Kept anyway: correct now, and
#: becomes load-bearing rather than dead code the moment either call site is
#: ever moved behind `run_in_threadpool`/`asyncio.to_thread`.
_lock = threading.Lock()
_state: dict[tuple[str, str], _ActionState] = {}


def record(
    *,
    component: str,
    event: str,
    action: str,
    exc: Exception,
    extra: dict[str, Any] | None = None,
) -> bool:
    """Note a fail-open event and log it, but not more than once per interval.

    `component` is which control degraded ("ratelimit", "login_throttle"),
    used only to group entries in `snapshot()`. `event` is the exact
    structlog event name the direct `log.warning()` calls this replaces
    already used ("ratelimit.storage_unavailable",
    "auth.throttle_unavailable", ...) - kept byte-identical so an existing
    saved search or dashboard built against that event name still matches.
    `action` is what was being attempted ("incr", "gate", ...). `extra` is
    merged into the log call when one is emitted, for a caller whose
    message needs to say more than "Redis is unavailable" (e.g.
    `login_throttle.spend_once`, where the operationally important fact is
    which specific protection was off, not only that Redis was down).

    Returns whether this call actually logged, so a caller that wants ITS
    OWN specific message instead of the generic one here can gate that
    message on the same decision rather than emitting an un-deduped line of
    its own (that gap - a second, un-deduped call site reproducing #419
    under a different event name - is exactly what a prior review round
    found in `spend_once`).
    """
    now = time.time()
    key = (component, action)
    with _lock:
        state = _state.setdefault(key, _ActionState())
        state.count += 1
        state.last_degraded_at = now
        should_log = now - state.last_logged_at >= _LOG_INTERVAL_SECONDS
        if should_log:
            suppressed = state.suppressed_since_last_log
            state.last_logged_at = now
            state.suppressed_since_last_log = 0
        else:
            state.suppressed_since_last_log += 1

    if should_log:
        log.warning(
            event,
            action=action,
            error=str(exc),
            suppressed_since_last_log=suppressed,
            **(extra or {}),
        )
    return should_log


def snapshot() -> dict[str, dict[str, float | int]]:
    """Per-component summary for `/health/ready`: total count and the most
    recent degradation across every action that component has recorded.

    Aggregated by component (not by the finer (component, action) key
    `record()` dedupes logging on) because "is login_throttle currently
    degraded" is the question a reader of `/health/ready` has, not "is
    login_throttle's `gate` action specifically degraded" - the actions
    within one component all point at the same underlying Redis.

    KNOWN LIMITATION (security review on #419/#420): this state is a plain
    module-level dict, one copy per OS process. The production image runs
    `uvicorn --workers ${UVICORN_WORKERS:-4}` (docker-compose.yml /
    Dockerfile.prod), so a real deployment has 4 independent copies, and
    `/health/ready` is answered by whichever worker's socket the kernel
    hands the connection to - effectively at random from the caller's side.
    A poll that lands on a worker that has not yet handled a rate-limited or
    login request reads `{}` here even while the other 3 workers are
    already permanently fail-open on a bad `REDIS_URL`. `_PID` is included
    per component precisely so a reader polling repeatedly can tell "this
    worker has not seen it" apart from "no worker has" instead of the two
    looking identical - true cross-process aggregation (a file, a shared
    counter) is not implemented, and Redis itself being the thing that is
    down rules out using it as that shared store for exactly this signal.
    """
    totals: dict[str, dict[str, float | int]] = {}
    with _lock:
        for (component, _action), state in _state.items():
            entry = totals.setdefault(
                component, {"count": 0, "last_degraded_at": 0.0, "worker_pid": _PID}
            )
            entry["count"] += state.count
            entry["last_degraded_at"] = max(
                entry["last_degraded_at"], state.last_degraded_at
            )
    return totals


def _reset_for_tests() -> None:
    """Test-only: clear all recorded state between test cases.

    Module-level state persists across tests in the same process otherwise,
    which would make one test's degraded call visible in another's snapshot.
    """
    with _lock:
        _state.clear()
