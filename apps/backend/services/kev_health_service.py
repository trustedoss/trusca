"""
KEV feed health service — Phase C (admin/health KEV feed panel).

Assembles the ``GET /v1/admin/kev/health`` payload from four sources:

  * the single ``kev_sync_state`` row (written by ``tasks/kev_catalog_refresh``
    at the end of every tick — see the writer contract in
    ``models/kev_sync_state.py``; row absent = the refresh has never run),
  * a live ``count(*)`` of ``vulnerabilities.kev = true`` rows (rides the
    partial index ``ix_vulnerabilities_kev``, so the scan is bounded by the
    ~1,600-entry CISA catalog, not the table size),
  * runtime config (``KEV_REFRESH_ENABLED`` toggle, ``KEV_FEED_URL`` host —
    read at call time per CLAUDE.md core rule #11),
  * the LIVE Celery beat schedule, from which the next fire time is derived
    (never a hardcoded copy of the cron spec — a cadence change in
    ``tasks/celery_app.py`` propagates here for free).

Structural mirror of ``services.trivy_health_service`` (graceful degrade to a
config-only payload so a DB hiccup never 500s the admin/health page), but
deliberately WITHOUT its 60s in-process cache: the Trivy service caches
because every read stats the worker's on-disk cache directory, whereas a read
here is one PK lookup on a single-row table plus one partial-index count on
the pooled session the endpoint already holds — two cheap indexed queries per
30s poll, so a cache would add invalidation surface (tests, stale-after-tick
windows) for no measurable saving.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlparse

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import kev_feed_url, kev_refresh_enabled
from models import KevSyncState, Vulnerability
from schemas.admin_ops import KevFeedStatusOut, KevSyncResult

log = structlog.get_logger("admin.kev_health.service")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _feed_host() -> str | None:
    """Host of the configured KEV feed URL — host only, never the full URL.

    The panel only needs "cisa.gov or an internal mirror?"; a mirror URL may
    carry path tokens the admin UI has no business displaying. ``hostname``
    (not ``netloc``) also drops any userinfo / port.
    """
    try:
        return urlparse(kev_feed_url()).hostname
    except ValueError:  # pragma: no cover — urlparse rejects almost nothing
        return None


def compute_next_refresh_at(now: datetime | None = None) -> datetime | None:
    """Next fire time of the KEV beat, derived from the live Celery schedule.

    Resolves the crontab object out of ``celery_app.conf.beat_schedule``
    under the shared ``KEV_BEAT_ENTRY_NAME`` key and asks IT for the next
    occurrence via ``remaining_delta`` — deterministic given ``now``
    (``remaining_estimate`` would consult the wall clock internally, which
    breaks unit-test pinning). Returns ``None`` when the entry cannot be
    resolved; a missing panel field is not worth a 500.
    """
    try:
        # Imported lazily so a broken Celery bootstrap (e.g. mis-set REDIS_URL
        # shape) degrades this one field instead of breaking module import.
        from tasks.celery_app import KEV_BEAT_ENTRY_NAME, celery_app

        entry = celery_app.conf.beat_schedule.get(KEV_BEAT_ENTRY_NAME)
        if not entry:
            return None
        schedule = entry["schedule"]
        reference = now if now is not None else _now()
        start, delta, _ = schedule.remaining_delta(reference)
        return cast(datetime, start + delta)
    except Exception as exc:  # noqa: BLE001 — panel field, never worth a 500
        log.warning("kev_next_refresh_derivation_failed", error=str(exc)[:300])
        return None


async def get_kev_feed_health(session: AsyncSession) -> KevFeedStatusOut:
    """Public entry point — the admin endpoint calls this.

    Row absent (feature never ran) is a legitimate state: every sync-derived
    field stays ``None`` while the config-derived fields (and the live KEV
    flag count) still render. Any DB failure degrades to the config-only
    payload with a WARNING — same graceful posture as
    ``trivy_health_service.get_trivy_db_health``.
    """
    enabled = kev_refresh_enabled()
    host = _feed_host()
    # Computed even when disabled: the beat entry still fires (each tick then
    # records a ``disabled`` skip), so "next attempt" remains truthful and the
    # FE can pair it with ``enabled=false``.
    next_refresh = compute_next_refresh_at()

    try:
        row = await session.get(KevSyncState, True)
        flagged = (
            await session.execute(
                select(func.count())
                .select_from(Vulnerability)
                .where(Vulnerability.kev.is_(True))
            )
        ).scalar_one()

        if row is None:
            return KevFeedStatusOut(
                enabled=enabled,
                kev_flagged_total=int(flagged),
                next_refresh_at=next_refresh,
                feed_host=host,
            )

        return KevFeedStatusOut(
            enabled=enabled,
            last_synced_at=row.last_synced_at,
            last_attempt_at=row.updated_at,
            # DB column is VARCHAR; the vocabulary is task-owned and closed
            # (model docstring). Pydantic re-validates at construction — an
            # out-of-vocabulary value lands in the except arm below.
            last_result=cast(KevSyncResult | None, row.last_result),
            skipped_reason=row.skipped_reason,
            feed_count=row.feed_count,
            listed=row.listed,
            delisted=row.delisted,
            duration_ms=row.duration_ms,
            kev_flagged_total=int(flagged),
            next_refresh_at=next_refresh,
            feed_host=host,
        )
    except Exception as exc:  # noqa: BLE001 — last-resort graceful degrade
        log.warning("kev_feed_health_read_failed", error=str(exc)[:300])
        return KevFeedStatusOut(
            enabled=enabled,
            next_refresh_at=next_refresh,
            feed_host=host,
        )


__all__ = [
    "compute_next_refresh_at",
    "get_kev_feed_health",
]
