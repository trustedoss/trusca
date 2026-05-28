"""
Trivy DB health service — W6-#43e (admin/health Trivy DB panel).

Wraps :func:`integrations.trivy.get_trivy_db_status` with a short in-process
cache (60s by default) because admin/health polls every 30s and the on-disk
``metadata.json`` only refreshes weekly. Without the cache, the panel would
re-stat the worker's Trivy cache directory on every poll for no signal gain.

The cache TTL is process-local — no Redis round-trip — because:
  * The on-disk metadata is monotonic between refreshes (only the W6-#44 beat
    rewrites it, and that runs at most weekly).
  * A stale 60s read against a fresh download is harmless; the panel polls
    again in 30s and the next snapshot picks it up.
  * Cross-process consistency does not matter — every FastAPI worker
    independently snapshots the same on-disk file.

W6-#43e scope is *status exposure only*; the W6-#44 follow-up owns the
weekly Celery beat that actually refreshes the DB.
"""

from __future__ import annotations

import time
from dataclasses import asdict

import structlog

from integrations.trivy import TrivyDbStatus, get_trivy_db_status
from schemas.admin_ops import TrivyDbStatusOut

log = structlog.get_logger("admin.trivy_health.service")

# 60s TTL is the sweet spot for admin/health polling (30s) — every other
# poll re-reads the metadata.json, which is essentially free, while still
# absorbing burst polls (multiple admin tabs open) without thrashing the
# filesystem. Bumping this past ~5 min would risk a freshly-downloaded DB
# showing stale in the panel for too long after a refresh.
_CACHE_TTL_SECONDS = 60.0


# Module-level cache state. We intentionally store the dataclass (not the
# pydantic model) so subsequent reads can re-validate and the FE schema can
# evolve without invalidating the cache shape. The tuple is (expires_at,
# status); the lock-free shape is safe because:
#   * Single FastAPI worker per process serialises reads on the event loop.
#   * A racing write at the TTL boundary just costs one extra stat() — no
#     correctness impact.
_cache: tuple[float, TrivyDbStatus] | None = None


def _clock() -> float:
    """Monotonic clock indirection for tests."""
    return time.monotonic()


def reset_cache() -> None:
    """Invalidate the cached status snapshot — used by unit tests."""
    global _cache
    _cache = None


def get_trivy_db_status_cached() -> TrivyDbStatus:
    """Return the cached snapshot, refreshing if older than ``_CACHE_TTL_SECONDS``."""
    global _cache
    now = _clock()
    if _cache is not None:
        expires_at, snapshot = _cache
        if now < expires_at:
            return snapshot
    snapshot = get_trivy_db_status()
    _cache = (now + _CACHE_TTL_SECONDS, snapshot)
    return snapshot


def get_trivy_db_health() -> TrivyDbStatusOut:
    """Public entry point — admin endpoint calls this.

    Pulls the cached snapshot, then constructs the Pydantic response model.
    Wrapped in a try/except so a transient I/O error (e.g. cache_dir is on a
    yanked mount) does not 500 the admin/health page — we surface a partial
    snapshot with ``freshness='unknown'`` instead.
    """
    try:
        snapshot = get_trivy_db_status_cached()
    except Exception as exc:  # noqa: BLE001 — last-resort graceful degrade
        log.warning("trivy_db_status_failed", error=str(exc))
        # Build a minimum-viable response so the FE renders the EmptyState
        # rather than the page-level error alert. We import lazily to keep
        # the happy path import cost flat.
        from integrations.trivy import (
            trivy_cache_dir,
            trivy_db_refresh_interval_hours,
            trivy_db_repository,
        )

        return TrivyDbStatusOut(
            last_update=None,
            next_refresh_at=None,
            vuln_count=None,
            db_version=None,
            db_size_bytes=None,
            refresh_interval_hours=trivy_db_refresh_interval_hours(),
            freshness="unknown",
            cache_dir=str(trivy_cache_dir()),
            repository=trivy_db_repository(),
        )

    return TrivyDbStatusOut(**asdict(snapshot))


__all__ = [
    "get_trivy_db_health",
    "get_trivy_db_status_cached",
    "reset_cache",
]
