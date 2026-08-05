# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Malicious snapshot health service — #26 admin/health panel.

Sibling of :mod:`services.eol_health_service`, assembling
``GET /v1/admin/malicious/health`` from three sources:

  * the single ``malicious_sync_state`` row (written by
    ``tasks/malicious_catalog_refresh`` at the end of every weekly tick),
  * the snapshot the evaluator currently has loaded — its date is the
    staleness signal,
  * a live count of ``component_versions.malicious_state = 'flagged'``
    (rides the partial index).

Degrades to a config-only payload rather than erroring: an operator opening
this panel is usually doing so because something looks wrong, and a 500 is
the least useful answer at that moment.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import cast

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import (
    malicious_enabled,
    malicious_refresh_enabled,
    malicious_snapshot_stale_days,
)
from models import ComponentVersion, MaliciousSyncState
from schemas.admin_ops import MaliciousStatusOut
from services.malicious import malicious_catalog

log = structlog.get_logger("admin.malicious_health.service")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def compute_next_refresh_at(now: datetime | None = None) -> datetime | None:
    """Next fire of the malicious beat, from the live schedule (KEV idiom)."""
    try:
        from tasks.celery_app import MALICIOUS_BEAT_ENTRY_NAME, celery_app

        entry = celery_app.conf.beat_schedule.get(MALICIOUS_BEAT_ENTRY_NAME)
        if not entry:
            return None
        schedule = entry["schedule"]
        reference = now if now is not None else _now()
        start, delta, _ = schedule.remaining_delta(reference)
        return cast(datetime, start + delta)
    except Exception as exc:  # noqa: BLE001 — panel field, never worth a 500
        log.warning(
            "malicious_next_refresh_derivation_failed", error=str(exc)[:300]
        )
        return None


def _is_stale(snapshot_date: date | None, now: datetime) -> bool:
    if snapshot_date is None:
        # Nothing loaded is a louder problem than a stale snapshot, and the
        # panel says so through the null date. Not doubly flagged as stale.
        return False
    return snapshot_date < (now - timedelta(days=malicious_snapshot_stale_days())).date()


async def get_malicious_health(session: AsyncSession) -> MaliciousStatusOut:
    """Assemble the admin panel payload."""
    now = _now()
    index = malicious_catalog.load_index()

    snapshot_date: date | None = None
    if index is not None:
        try:
            snapshot_date = date.fromisoformat(index.snapshot)
        except ValueError:
            snapshot_date = None

    flagged_total: int | None = None
    row: MaliciousSyncState | None = None
    try:
        flagged_total = int(
            (
                await session.execute(
                    select(func.count()).where(
                        ComponentVersion.malicious_state == "flagged"
                    )
                )
            ).scalar_one()
        )
        row = (
            await session.execute(select(MaliciousSyncState).limit(1))
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — degrade, do not 500
        log.warning("malicious_health_db_read_failed", error=str(exc)[:300])

    return MaliciousStatusOut(
        enabled=malicious_enabled(),
        refresh_enabled=malicious_refresh_enabled(),
        snapshot_date=snapshot_date,
        snapshot_stale=_is_stale(snapshot_date, now),
        purl_count=len(index.packages) if index else 0,
        ecosystems=list(index.ecosystems) if index else [],
        flagged_total=flagged_total,
        last_synced_at=row.last_synced_at if row else None,
        last_attempt_at=row.updated_at if row else None,
        last_result=row.last_result if row else None,  # type: ignore[arg-type]
        skipped_reason=row.skipped_reason if row else None,
        stamped=row.stamped if row else None,
        newly_flagged=row.newly_flagged if row else None,
        next_refresh_at=compute_next_refresh_at(now),
    )


__all__ = ["compute_next_refresh_at", "get_malicious_health"]
