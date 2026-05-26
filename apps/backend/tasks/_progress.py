"""
Scan-progress Redis publisher — Phase 2 PR #9.

The DB row on ``scans`` is the authoritative store for scan progress
(``current_step`` / ``progress_percent``). This module is a *secondary*
pub/sub channel that lets WebSocket-connected clients see progress as it
happens without polling. The WebSocket gateway in :mod:`api.v1.ws`
subscribes to ``scan:<scan_id>:progress`` (see :func:`core.config.scan_progress_channel`)
and forwards every payload to the connected user.

Design notes:

  - **Fire-and-forget.** Publish failures must NEVER break a scan. The DB
    is the single source of truth — a missed publish degrades to "client
    polls and the next stage commit publishes again". We swallow Redis
    exceptions and emit a ``log.warning`` instead.
  - **Sync API.** Celery tasks run in sync Python, so we use the sync
    redis-py client (the same family as :mod:`integrations.dt.breaker`).
  - **Lazy singleton client.** A worker handles many scans; reusing a
    single connection avoids per-publish TCP handshake. The singleton is
    keyed on the resolved ``REDIS_URL`` so a runtime env change still
    forces a fresh client (CLAUDE.md core rule #11 — no module-level env
    caching).
  - **Test hook.** ``reset_publisher_for_tests`` clears the singleton so
    fakeredis-backed unit tests get a fresh client each case.

Message schemas (canonical):

    Progress frame (existing):
        {
            "type":    "progress",          # P2 #8c addition (backward-compat:
                                            #   absence is interpreted as "progress")
            "percent": <int 0-100>,
            "step":    <str>,
            "ts":      <ISO 8601 UTC, e.g. "2026-05-06T11:24:31.123456+00:00">
        }

    Log frame (P2 #8c):
        {
            "type":   "log",
            "stage":  "cdxgen" | "scancode" | ...,   # which tool produced the line
            "stream": "stdout" | "stderr",
            "line":   <str>,                          # capped at SCAN_LOG_LINE_MAX_LEN
            "ts":     <ISO 8601 UTC>
        }

Both are encoded as UTF-8 JSON bytes; the WS gateway decodes with strict UTF-8.

Step value vocabulary (do not free-text):

    Source pipeline (in-progress):
        bootstrap, fetch, cdxgen, ort, dt_upload, dt_findings, finalize
    Container pipeline (in-progress):
        bootstrap, trivy, persist, finalize
    Terminal (both):
        succeeded, failed
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime

import redis
import structlog

from core.config import (
    redis_url,
    scan_log_line_max_len,
    scan_log_max_lines_per_scan,
    scan_progress_channel,
)

log = structlog.get_logger("tasks.progress")


# ---------------------------------------------------------------------------
# Per-scan log line counter (P2 #8c)
# ---------------------------------------------------------------------------
#
# ``scan_log_max_lines_per_scan()`` caps the number of log lines we will publish
# for a single scan. The counter has to live in process memory because the
# cdxgen / scancode subprocesses publish from worker threads inside a Celery
# task, and we want every line drain thread for the SAME scan to share one
# counter (so cdxgen + scancode together cannot evade the cap). It is keyed on
# the scan id string so concurrent scans inside the same worker (rare, but
# Celery supports it) each get their own budget.
#
# We do NOT use Redis here — the cap is a publish-time safety net, not a
# distributed coordination problem, and Redis round-trips on every line would
# be more expensive than the publish itself. A scan that briefly crosses the
# cap on a different worker (after a worker restart) is acceptable: the lines
# the user actually saw came from the original worker.
# ---------------------------------------------------------------------------

_log_counts: dict[str, int] = {}
_log_counts_lock = threading.Lock()


def reset_log_counter(scan_id: uuid.UUID | str) -> None:
    """Forget the published-line count for ``scan_id``.

    Called by the task entry point on every (re-)run so a retried scan gets a
    fresh budget. Idempotent: missing keys are silently ignored.
    """
    key = str(scan_id)
    with _log_counts_lock:
        _log_counts.pop(key, None)


def _bump_log_counter(scan_id_str: str, *, limit: int) -> bool:
    """Try to consume one slot from the per-scan log budget.

    Returns True when the publish may proceed, False when the budget is
    exhausted (the publisher then silently drops the line). A non-positive
    limit acts as a kill switch — the function always returns False.
    """
    if limit <= 0:
        return False
    with _log_counts_lock:
        current = _log_counts.get(scan_id_str, 0)
        if current >= limit:
            return False
        _log_counts[scan_id_str] = current + 1
        return True


# ---------------------------------------------------------------------------
# Lazy singleton — see module docstring for rationale.
# ---------------------------------------------------------------------------

_client: redis.Redis | None = None
_client_url: str | None = None


def _get_client() -> redis.Redis:
    """Return a process-wide redis-py client.

    Resolved url is captured alongside the client so a test (or operator
    rotating REDIS_URL) gets a fresh connection rather than a stale one
    pointing at the previous broker. This keeps us honest with CLAUDE.md
    core rule #11 while still amortising connect cost across publishes.
    """
    global _client, _client_url
    url = redis_url()
    if _client is None or _client_url != url:
        # decode_responses=False — we publish bytes payloads (JSON-encoded
        # UTF-8) and let the subscriber side decode. This matches the WS
        # gateway's expectations and avoids accidental string round-trips.
        _client = redis.Redis.from_url(url, decode_responses=False)
        _client_url = url
    return _client


def reset_publisher_for_tests() -> None:
    """Drop the cached client so the next call rebuilds it.

    Unit tests inject a fakeredis instance via monkeypatch on this module's
    ``_get_client`` (or by monkeypatching ``redis.Redis.from_url``); calling
    this in a fixture teardown keeps state from leaking between test cases.
    """
    global _client, _client_url
    if _client is not None:
        try:
            _client.close()  # type: ignore[no-untyped-call]
        except Exception as exc:  # pragma: no cover — close errors are best-effort
            log.debug("scan_progress_client_close_failed", error=str(exc))
    _client = None
    _client_url = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """ISO-8601 UTC timestamp, microsecond precision."""
    return datetime.now(UTC).isoformat()


def publish_progress(
    scan_id: uuid.UUID | str,
    *,
    step: str,
    percent: int,
) -> None:
    """Publish a single progress event to ``scan:<scan_id>:progress``.

    Fire-and-forget: any Redis-side error is swallowed and logged at
    ``WARNING`` so the scan pipeline keeps running. The DB row is the
    single source of truth; pub/sub is best-effort.

    Args:
        scan_id: UUID (object or stringified) — must match the value the
            WebSocket gateway uses to subscribe.
        step:   Stage identifier (``bootstrap``, ``cdxgen``, ``succeeded``,
            etc.). See module docstring for the vocabulary.
        percent: 0-100 progress integer. Clamped to that range so a
            misconfigured caller cannot poison the UI.
    """
    # All serialization happens inside the try so a misbehaving __str__ on
    # the scan_id (or a JSON encoder hiccup) cannot crash the scan. The DB
    # row remains the single source of truth — a missed publish is recovered
    # from on the next stage commit.
    try:
        scan_id_str = str(scan_id)
        clamped = max(0, min(100, int(percent)))
        payload = {
            # P2 #8c — explicit type discriminator. Older clients that ignore
            # the field still see {percent, step, ts} as before; new clients
            # use it to fan out progress vs log frames on the wire. The
            # default-on-absence interpretation is "progress" so a frame
            # forwarded from an older worker remains compatible.
            "type": "progress",
            "percent": clamped,
            "step": step,
            "ts": _now_iso(),
        }
        channel = scan_progress_channel(scan_id_str)
        body = json.dumps(payload).encode("utf-8")
        client = _get_client()
        client.publish(channel, body)
    except Exception as exc:  # broad: redis errors, network, serialization
        # CLAUDE.md core rule #4 doesn't apply (Redis broker, not DT) — but
        # the same "best-effort, never crash the scan" principle holds.
        log.warning(
            "scan_progress_publish_failed",
            scan_id=repr(scan_id),
            step=step,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# P2 #8c — tool log line publisher
# ---------------------------------------------------------------------------


_TRUNC_SUFFIX = "…(truncated)"


def _truncate_line(line: str, limit: int) -> str:
    """Bound a single tool log line at ``limit`` chars.

    A hostile / runaway subprocess might emit a pathological multi-MB single
    line (no newline) — we never want the worker to materialise that as a
    single Redis publish. We slice to ``limit - len(_TRUNC_SUFFIX)`` and tag
    the result so the consumer sees a truncation happened. ``limit`` < 1
    falls back to a hard ``""`` (kill-switch friendly).
    """
    if limit <= 0:
        return ""
    if len(line) <= limit:
        return line
    keep = max(0, limit - len(_TRUNC_SUFFIX))
    return line[:keep] + _TRUNC_SUFFIX


_VALID_STREAMS: frozenset[str] = frozenset({"stdout", "stderr"})


def publish_log(
    scan_id: uuid.UUID | str,
    *,
    stage: str,
    stream: str,
    line: str,
) -> None:
    """Publish a single tool log line to ``scan:<scan_id>:progress``.

    Fire-and-forget, same contract as :func:`publish_progress`: any Redis-side
    error is swallowed and logged at WARNING so the scan pipeline keeps
    running. The structlog ``log.warning`` text never contains the raw line
    (the line could carry secrets if a misbehaving tool ever echoed them).

    Args:
        scan_id: UUID (object or stringified) — must match the value the
            WebSocket gateway uses to subscribe.
        stage:   Pipeline stage that produced the line. Caller is expected to
            use the canonical step vocabulary (``cdxgen``, ``scancode``, …)
            so the FE can color-code consistently.
        stream:  ``"stdout"`` or ``"stderr"``. Any other value is normalised
            to ``"stdout"`` (defensive — never block a publish over a typo).
        line:    The raw line text. Truncated to ``SCAN_LOG_LINE_MAX_LEN``
            chars and bounded per-scan by ``SCAN_LOG_MAX_LINES_PER_SCAN``.

    The per-scan publish cap is enforced inside this helper, BEFORE
    serialization, so an over-cap line never touches Redis. The cap is shared
    across all stages of a single scan (cdxgen + scancode together) so a
    runaway subprocess cannot evade the limit by racing another stage.
    """
    try:
        scan_id_str = str(scan_id)
        line_limit = scan_log_line_max_len()
        scan_limit = scan_log_max_lines_per_scan()

        # Per-scan budget check FIRST (cheapest), so an over-cap publish never
        # hits Redis or even the line-truncation logic.
        if not _bump_log_counter(scan_id_str, limit=scan_limit):
            return

        safe_stream = stream if stream in _VALID_STREAMS else "stdout"
        safe_line = _truncate_line(str(line), line_limit)

        payload = {
            "type": "log",
            "stage": str(stage),
            "stream": safe_stream,
            "line": safe_line,
            "ts": _now_iso(),
        }
        channel = scan_progress_channel(scan_id_str)
        body = json.dumps(payload).encode("utf-8")
        client = _get_client()
        client.publish(channel, body)
    except Exception as exc:  # broad: redis errors, network, serialization
        # Same philosophy as publish_progress — best-effort, never crash a
        # scan over a log-streaming hiccup. We DELIBERATELY do not log the
        # `line` content: it can carry attacker-controlled bytes and we
        # already have a length-bounded payload escape valve above.
        log.warning(
            "scan_log_publish_failed",
            scan_id=repr(scan_id),
            stage=stage,
            stream=stream,
            error=str(exc),
        )


__all__ = [
    "publish_log",
    "publish_progress",
    "reset_log_counter",
    "reset_publisher_for_tests",
]
