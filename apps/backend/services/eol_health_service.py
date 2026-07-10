"""
EOL snapshot health service — Phase M (admin/health EOL panel).

Assembles the ``GET /v1/admin/eol/health`` payload from four sources
(structural mirror of :mod:`services.kev_health_service`, including the
graceful degrade to a config-only payload and the no-cache reasoning):

  * the single ``eol_sync_state`` row (written by
    ``tasks/eol_catalog_refresh`` at the end of every weekly tick),
  * the EFFECTIVE dataset — the newer of the vendored/override snapshot and
    the last fetched one — whose date is the panel's staleness signal,
  * a live count of ``component_versions.eol_state = 'eol'`` rows (rides
    the partial index ``ix_component_versions_eol``),
  * runtime config + the LIVE Celery beat schedule (next fire derived from
    the crontab under the shared ``EOL_BEAT_ENTRY_NAME``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal, cast
from urllib.parse import urlsplit

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import eol_enabled, eol_feed_url_template, eol_refresh_enabled
from models import ComponentVersion, EolSyncState
from schemas.admin_ops import EolStatusOut, EolSyncResult
from services.eol.eol_catalog import load_dataset, load_rules

log = structlog.get_logger("admin.eol_health.service")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _feed_host() -> str | None:
    """Host of the feed URL template — host only, never the full URL."""
    try:
        return urlsplit(
            eol_feed_url_template().replace("{product}", "probe")
        ).hostname
    except ValueError:  # pragma: no cover — urlsplit rejects almost nothing
        return None


def compute_next_refresh_at(now: datetime | None = None) -> datetime | None:
    """Next fire of the EOL beat, from the live schedule (KEV idiom)."""
    try:
        from tasks.celery_app import EOL_BEAT_ENTRY_NAME, celery_app

        entry = celery_app.conf.beat_schedule.get(EOL_BEAT_ENTRY_NAME)
        if not entry:
            return None
        schedule = entry["schedule"]
        reference = now if now is not None else _now()
        start, delta, _ = schedule.remaining_delta(reference)
        return cast(datetime, start + delta)
    except Exception as exc:  # noqa: BLE001 — panel field, never worth a 500
        log.warning("eol_next_refresh_derivation_failed", error=str(exc)[:300])
        return None


def _parse_snapshot_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _effective_snapshot(
    row: EolSyncState | None,
) -> tuple[date | None, Literal["vendored", "feed"] | None, int]:
    """(date, origin, product_count) of the newer dataset — vendored vs fetched."""
    vendored = load_dataset()
    vendored_date = _parse_snapshot_date(vendored.snapshot) if vendored else None
    fetched_date = row.snapshot_date if row is not None else None

    if vendored_date is None and fetched_date is None:
        return None, None, 0
    if fetched_date is not None and (
        vendored_date is None or fetched_date >= vendored_date
    ):
        products = 0
        if row is not None and isinstance(row.snapshot, dict):
            products = sum(1 for k in row.snapshot if not k.startswith("_"))
        return fetched_date, "feed", products
    assert vendored is not None  # implied by vendored_date is not None
    return vendored_date, "vendored", len(vendored.products)


async def get_eol_health(session: AsyncSession) -> EolStatusOut:
    """Public entry point — the admin endpoint calls this.

    Row absent (beat never ran) is a legitimate state; DB failures degrade
    to a config-plus-vendored payload with a WARNING.
    """
    enabled = eol_enabled()
    refresh_enabled = eol_refresh_enabled()
    host = _feed_host()
    next_refresh = compute_next_refresh_at()
    rule_count = len(load_rules())

    try:
        row = await session.get(EolSyncState, True)
        flagged = (
            await session.execute(
                select(func.count())
                .select_from(ComponentVersion)
                .where(ComponentVersion.eol_state == "eol")
            )
        ).scalar_one()
        snapshot_date, origin, product_count = _effective_snapshot(row)

        if row is None:
            return EolStatusOut(
                enabled=enabled,
                refresh_enabled=refresh_enabled,
                snapshot_date=snapshot_date,
                snapshot_origin=origin,
                rule_count=rule_count,
                product_count=product_count,
                eol_flagged_total=int(flagged),
                next_refresh_at=next_refresh,
                feed_host=host,
            )

        return EolStatusOut(
            enabled=enabled,
            refresh_enabled=refresh_enabled,
            snapshot_date=snapshot_date,
            snapshot_origin=origin,
            rule_count=rule_count,
            product_count=product_count,
            eol_flagged_total=int(flagged),
            last_synced_at=row.last_synced_at,
            last_attempt_at=row.updated_at,
            last_result=cast(EolSyncResult | None, row.last_result),
            skipped_reason=row.skipped_reason,
            stamped=row.stamped,
            cleared=row.cleared,
            duration_ms=row.duration_ms,
            next_refresh_at=next_refresh,
            feed_host=host,
        )
    except Exception as exc:  # noqa: BLE001 — last-resort graceful degrade
        log.warning("eol_health_read_failed", error=str(exc)[:300])
        snapshot_date, origin, product_count = _effective_snapshot(None)
        return EolStatusOut(
            enabled=enabled,
            refresh_enabled=refresh_enabled,
            snapshot_date=snapshot_date,
            snapshot_origin=origin,
            rule_count=rule_count,
            product_count=product_count,
            next_refresh_at=next_refresh,
            feed_host=host,
        )


__all__ = [
    "compute_next_refresh_at",
    "get_eol_health",
]
