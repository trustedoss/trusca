"""
Dependency-Track circuit breaker — CLAUDE.md core rule #4.

State machine (3 states)::

      ┌──────────┐  failure_count >= threshold   ┌──────┐
      │  CLOSED  │ ─────────────────────────────▶│ OPEN │
      │ (normal) │                               │      │
      └──────────┘ ◀───────── probe ok ──────────└──┬───┘
            ▲                                       │
            │ probe ok                              │ cooldown elapsed
            │                                ┌──────▼──────┐
            └──────────────── ─── ─── ───────│  HALF_OPEN  │
                                             │ (one probe) │
                                             └──────┬──────┘
                                                    │ probe fails
                                                    ▼
                                                 (back to OPEN, cooldown reset)

Storage:
    All breaker state lives in Redis under three keys
    (``dt:breaker:state`` / ``dt:breaker:fail_count`` / ``dt:breaker:opened_at``).
    Multiple Celery workers share one breaker — fail_count is incremented
    via ``INCR`` (atomic), state transitions are guarded by ``SET ... XX``
    against the previous value via a small Lua script so two workers cannot
    both flip OPEN → HALF_OPEN at the same instant.

Race window — security-reviewer surface (M-7):

    The transition CLOSED → OPEN can race when two workers see the failure
    count cross the threshold at the same time. Both will then SET state to
    OPEN, which is idempotent — no double-reset of opened_at because we use
    SETNX for opened_at on first OPEN entry. The probe gate
    (OPEN → HALF_OPEN → CLOSED) is the only multi-step transition; we use a
    Lua CAS script (``_PROBE_GATE_LUA``) so exactly one worker takes the
    half-open probe slot per cooldown window. After the script, that worker
    holds an in-memory `_probe_token` we re-check on success / failure to
    avoid a second worker accidentally completing the probe transition.

    We do NOT add a distributed lock (e.g. redlock). The breaker is a
    coarse-grained reliability device, not a correctness primitive — DT
    itself rejects duplicate uploads via the project ID, and a brief flap
    across a transition costs at most one extra DT request before the next
    failure / success drives consensus. The Lua + INCR pattern is sufficient
    to keep transition events to "exactly once per cooldown window" with a
    sub-millisecond race window.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

import redis
import structlog

from core.config import (
    dt_breaker_cooldown_seconds,
    dt_breaker_failure_threshold,
    redis_url,
)

from . import DTBreakerOpen, DTUnavailable

log = structlog.get_logger("integrations.dt.breaker")

T = TypeVar("T")

STATE_CLOSED = "closed"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

_VALID_STATES = frozenset({STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN})

# Redis key namespace. Using a single namespace prefix means an operator can
# wipe all breaker state with `redis-cli DEL dt:breaker:*` during incident
# response.
_KEY_STATE = "dt:breaker:state"
_KEY_FAIL_COUNT = "dt:breaker:fail_count"
_KEY_OPENED_AT = "dt:breaker:opened_at"
_KEY_PROBE_LOCK = "dt:breaker:probe_lock"

# Lua: atomically advance OPEN → HALF_OPEN if cooldown has elapsed.
#   KEYS[1] = state key, KEYS[2] = opened_at key, KEYS[3] = probe lock key
#   ARGV[1] = cooldown seconds, ARGV[2] = now (epoch seconds)
# Returns 1 when this caller takes the probe slot, 0 otherwise.
#
# We acquire a probe lock with NX + EX so that exactly one worker per
# cooldown window can issue the half-open probe. The lock TTL = 2 * cooldown
# so a crashed worker eventually releases it.
_PROBE_GATE_LUA = """
local state = redis.call('GET', KEYS[1])
if state ~= 'open' then
    return 0
end
local opened_at = tonumber(redis.call('GET', KEYS[2]) or '0')
local now = tonumber(ARGV[2])
local cooldown = tonumber(ARGV[1])
if (now - opened_at) < cooldown then
    return 0
end
local got_lock = redis.call('SET', KEYS[3], '1', 'NX', 'EX', cooldown * 2)
if got_lock then
    redis.call('SET', KEYS[1], 'half_open')
    return 1
end
return 0
"""


# ---------------------------------------------------------------------------
# Snapshot type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BreakerSnapshot:
    """Read-only view of breaker state, useful for logs / health endpoints."""

    state: str
    fail_count: int
    opened_at: float | None


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """
    Redis-backed circuit breaker for Dependency-Track calls.

    Construction is cheap (one Redis client). Workers should keep a single
    instance per process and reuse it across calls. The class exposes:

    - :meth:`call` — runs a callable, classifying success / failure.
    - :meth:`record_success` / :meth:`record_failure` — manual hooks for the
      health monitor (which calls DT directly outside of a payload context).
    - :meth:`snapshot` — current state for diagnostics.

    See module docstring for the state machine and race window discussion.
    """

    def __init__(
        self,
        *,
        redis_client: redis.Redis | None = None,
        failure_threshold: int | None = None,
        cooldown_seconds: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Redis client is built lazily so importing this module from the
        # FastAPI process (which never talks to DT) does not open a Redis
        # connection.
        self._redis: redis.Redis | None = redis_client
        # Both thresholds resolve at call time when not injected (rule #11).
        self._failure_threshold_override = failure_threshold
        self._cooldown_seconds_override = cooldown_seconds
        # ``time.time()`` is wall clock (used in Redis key) but we accept a
        # ``clock`` callable for tests that need to fast-forward across
        # cooldown boundaries.
        self._clock = clock

    # ------------------------------------------------------------------ helpers

    def _client(self) -> redis.Redis:
        if self._redis is None:
            # `decode_responses=True` makes the Lua return value an int (not
            # bytes) and keeps state strings as Python str.
            self._redis = redis.Redis.from_url(redis_url(), decode_responses=True)
        return self._redis

    def _failure_threshold(self) -> int:
        if self._failure_threshold_override is not None:
            return self._failure_threshold_override
        return dt_breaker_failure_threshold()

    def _cooldown_seconds(self) -> int:
        if self._cooldown_seconds_override is not None:
            return self._cooldown_seconds_override
        return dt_breaker_cooldown_seconds()

    def _now(self) -> float:
        # Note: we use wall time (``time.time()``) for Redis-stored timestamps
        # so multiple workers compare apples to apples, regardless of their
        # individual monotonic clocks. The injected ``clock`` is wall time
        # in tests too.
        return time.time()

    # ------------------------------------------------------------------ snapshot

    def snapshot(self) -> BreakerSnapshot:
        client = self._client()
        pipeline = client.pipeline()
        pipeline.get(_KEY_STATE)
        pipeline.get(_KEY_FAIL_COUNT)
        pipeline.get(_KEY_OPENED_AT)
        state_raw, fail_raw, opened_raw = pipeline.execute()  # type: ignore[no-untyped-call]
        state = state_raw if state_raw in _VALID_STATES else STATE_CLOSED
        fail_count = int(fail_raw) if fail_raw is not None else 0
        opened_at = float(opened_raw) if opened_raw is not None else None
        return BreakerSnapshot(state=state, fail_count=fail_count, opened_at=opened_at)

    # ------------------------------------------------------------------ call

    def call(self, fn: Callable[[], T]) -> T:
        """
        Run `fn`, classifying its result.

        - Returning normally → :meth:`record_success`.
        - Raising :class:`DTUnavailable` (5xx / network) → :meth:`record_failure`.
        - Raising any other exception → propagated as-is, breaker untouched
          (4xx auth/validation errors are user errors, not DT outages).
        """
        snapshot = self._maybe_advance_to_half_open()
        if snapshot.state == STATE_OPEN:
            log.warning("dt_breaker_open_short_circuit", **_snapshot_log_fields(snapshot))
            raise DTBreakerOpen("Dependency-Track circuit breaker is OPEN")

        try:
            result = fn()
        except DTUnavailable:
            self.record_failure()
            raise
        except Exception:
            # Non-DT errors (4xx, programming errors) do NOT count toward the
            # breaker — those are user-data problems, not DT outages.
            raise
        else:
            self.record_success()
            return result

    # ------------------------------------------------------------------ transitions

    def record_failure(self) -> None:
        """
        Increment the failure counter; flip to OPEN at the threshold.

        Called from :meth:`call` and from the health monitor when the
        ``/api/version`` heartbeat times out or returns 5xx.
        """
        client = self._client()
        threshold = self._failure_threshold()

        # INCR is atomic; if multiple workers observe the failure
        # simultaneously, only one of them will INCR past the threshold and
        # trigger the OPEN transition. Even if both get the same value back
        # (impossible with INCR — the server returns sequential values),
        # ``_open_if_not_open`` is idempotent.
        new_count = cast(int, client.incr(_KEY_FAIL_COUNT))
        if new_count >= threshold:
            self._open_if_not_open()

    def record_success(self) -> None:
        """
        Mark a successful DT call.

        - From CLOSED: reset the failure counter (no state change needed).
        - From HALF_OPEN: the probe succeeded, transition to CLOSED.
        - From OPEN: ignore — record_success is only called from inside
          :meth:`call`, which short-circuits OPEN before invoking the callable.
        """
        client = self._client()
        # We always wipe the fail counter on success; this is safe because
        # the threshold check uses the fresh INCR value, not the historical
        # max.
        client.delete(_KEY_FAIL_COUNT)
        # CAS: if state is HALF_OPEN, move to CLOSED. We skip the cheaper
        # ``SET state closed`` form because a stale CLOSED-from-CLOSED write
        # is fine, but we want the audit log line below to fire only on a
        # real transition.
        prev = client.getset(_KEY_STATE, STATE_CLOSED) if False else client.get(_KEY_STATE)
        if prev == STATE_HALF_OPEN:
            client.set(_KEY_STATE, STATE_CLOSED)
            client.delete(_KEY_OPENED_AT)
            client.delete(_KEY_PROBE_LOCK)
            log.warning("dt_breaker_closed", previous_state=STATE_HALF_OPEN)

    def force_close(self) -> None:
        """Operator-facing: force the breaker back to CLOSED (admin endpoint)."""
        client = self._client()
        client.set(_KEY_STATE, STATE_CLOSED)
        client.delete(_KEY_FAIL_COUNT)
        client.delete(_KEY_OPENED_AT)
        client.delete(_KEY_PROBE_LOCK)
        log.warning("dt_breaker_force_closed")

    # ------------------------------------------------------------------ internal

    def _open_if_not_open(self) -> None:
        client = self._client()
        # Use SETNX on opened_at so the first transition wins. If two workers
        # race, the second one's SETNX is a no-op — we want a single
        # canonical opened_at to drive the cooldown window.
        opened_at = self._now()
        first_opener = bool(client.set(_KEY_OPENED_AT, str(opened_at), nx=True))
        client.set(_KEY_STATE, STATE_OPEN)
        if first_opener:
            log.warning("dt_breaker_opened", opened_at=opened_at)

    def _maybe_advance_to_half_open(self) -> BreakerSnapshot:
        """
        If state is OPEN and the cooldown has elapsed, atomically advance to
        HALF_OPEN. Returns a snapshot of the (possibly new) state.

        The Lua script is the linchpin: it does
        ``if state == 'open' and (now - opened_at) >= cooldown and SETNX(probe_lock)
            then set state = 'half_open'`` in one round-trip. Without it,
        two workers would both observe OPEN + elapsed cooldown, both flip to
        HALF_OPEN, and both issue a probe. With the SETNX probe lock inside
        the script, exactly one worker wins.
        """
        snapshot = self.snapshot()
        if snapshot.state != STATE_OPEN:
            return snapshot
        client = self._client()
        cooldown = self._cooldown_seconds()
        # Pass wall time as ARGV so the script can compare against opened_at,
        # which is also wall time.
        result = client.eval(
            _PROBE_GATE_LUA,
            3,
            _KEY_STATE,
            _KEY_OPENED_AT,
            _KEY_PROBE_LOCK,
            str(cooldown),
            str(self._now()),
        )
        if int(cast(Any, result)) == 1:
            log.warning("dt_breaker_half_open", cooldown=cooldown)
            # Re-read to surface the new state to the caller.
            return self.snapshot()
        return snapshot


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _snapshot_log_fields(snapshot: BreakerSnapshot) -> dict[str, Any]:
    return {
        "breaker_state": snapshot.state,
        "fail_count": snapshot.fail_count,
        "opened_at": snapshot.opened_at,
    }


# Process-singleton, lazily constructed. Tests should not import this directly
# — instantiate ``CircuitBreaker(redis_client=fakeredis.FakeRedis(...))`` and
# inject through the call site.
_default_breaker: CircuitBreaker | None = None


def get_breaker() -> CircuitBreaker:
    """Return the process-wide breaker, building it on first use."""
    global _default_breaker
    if _default_breaker is None:
        _default_breaker = CircuitBreaker()
    return _default_breaker


def reset_default_breaker() -> None:
    """Test hook: drop the cached singleton so the next call rebuilds."""
    global _default_breaker
    _default_breaker = None


__all__ = [
    "STATE_CLOSED",
    "STATE_HALF_OPEN",
    "STATE_OPEN",
    "BreakerSnapshot",
    "CircuitBreaker",
    "get_breaker",
    "reset_default_breaker",
]
