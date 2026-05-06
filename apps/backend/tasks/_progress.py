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

Message schema (canonical):

    {
        "percent": <int 0-100>,
        "step":    <str>,
        "ts":      <ISO 8601 UTC, e.g. "2026-05-06T11:24:31.123456+00:00">
    }

Encoded as UTF-8 JSON bytes; the WS gateway decodes with strict UTF-8.

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
import uuid
from datetime import UTC, datetime

import redis
import structlog

from core.config import redis_url, scan_progress_channel

log = structlog.get_logger("tasks.progress")


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


__all__ = [
    "publish_progress",
    "reset_publisher_for_tests",
]
