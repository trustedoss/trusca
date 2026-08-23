# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Handing the audit trail to whatever collects logs (N17).

Off unless a destination is configured, and the audit API is unchanged either
way. This adds a way to push the trail somewhere; it takes nothing away from
the way an administrator reads it in the portal.

Two properties carry the design and both are asserted rather than assumed.

Nothing is sent twice and nothing is skipped. The position is a pair,
``(created_at, id)``, because audit rows share a millisecond often enough that
"everything after this instant" either repeats the tail of a batch or steps
over it. Resuming compares against the pair, so a batch that ended in the
middle of a crowded millisecond continues from exactly where it stopped.

And the export never reads the present. A row is stamped when its transaction
commits, so a transaction that began earlier can commit later and land behind
a position already passed; that row would never be sent and nothing would say
so. The export therefore stays a configured number of seconds behind now,
reading only stretches of time no open transaction can still write into.

The position lives in its own table. ``audit_logs`` is append-only with a
trigger enforcing it, and a column there would mean carving an exception into
the property that makes the trail worth having.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from core.config import (
    audit_export_batch_size,
    audit_export_lag_seconds,
    audit_export_url,
)
from models import AuditExportCursor, AuditLog

log = structlog.get_logger("services.audit_export")

#: Bumped when a field changes meaning or disappears. Adding one does not bump
#: it; a receiver that ignores unknown keys keeps working.
BATCH_VERSION = 1


@dataclass(frozen=True)
class Batch:
    """One run's worth of rows, and where they end."""

    rows: list[dict[str, Any]]
    last_created_at: datetime | None
    last_id: uuid.UUID | None

    @property
    def is_empty(self) -> bool:
        return not self.rows


def _row_to_dict(row: AuditLog) -> dict[str, Any]:
    """One audit row, as the collector receives it.

    The same columns the audit screen shows, including ``diff``, which the
    audit listener has already masked: passwords, tokens and API keys never
    reach the column, so they cannot reach here either. Nothing is re-derived
    or re-formatted, so a receiver comparing an exported row against the API
    sees the same values.
    """
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
        "team_id": str(row.team_id) if row.team_id else None,
        "action": row.action,
        "target_table": row.target_table,
        "target_id": row.target_id,
        "request_id": row.request_id,
        "ip": str(row.ip) if row.ip else None,
        "user_agent": row.user_agent,
        "diff": row.diff,
    }


def get_or_create_cursor(session: Session, *, destination: str) -> AuditExportCursor:
    """The position for one destination, creating it at the beginning.

    A fresh cursor starts at NULL rather than at now, so a deployment that
    switches this on hands over the trail it already has. An export that
    silently began at the moment it was configured would leave a hole nobody
    would think to look for.
    """
    cursor = session.execute(
        select(AuditExportCursor).where(AuditExportCursor.destination == destination)
    ).scalar_one_or_none()
    if cursor is not None:
        return cursor
    cursor = AuditExportCursor(destination=destination)
    session.add(cursor)
    session.commit()
    session.refresh(cursor)
    return cursor


def collect_batch(
    session: Session,
    *,
    cursor: AuditExportCursor,
    now: datetime | None = None,
) -> Batch:
    """Rows after the cursor and old enough to be settled.

    Ordered by the same pair the position is stored as, so the end of one
    batch is the start of the next with nothing between them.
    """
    horizon = (now or datetime.now(tz=UTC)) - timedelta(
        seconds=audit_export_lag_seconds()
    )
    stmt = select(AuditLog).where(AuditLog.created_at <= horizon)
    if cursor.last_created_at is not None and cursor.last_id is not None:
        # Strictly after the pair: the same timestamp is fine as long as the
        # id is larger, which is what keeps a crowded millisecond from being
        # either repeated or stepped over.
        stmt = stmt.where(
            or_(
                AuditLog.created_at > cursor.last_created_at,
                and_(
                    AuditLog.created_at == cursor.last_created_at,
                    AuditLog.id > cursor.last_id,
                ),
            )
        )
    stmt = stmt.order_by(AuditLog.created_at.asc(), AuditLog.id.asc()).limit(
        audit_export_batch_size()
    )

    rows = list(session.execute(stmt).scalars().all())
    if not rows:
        return Batch(rows=[], last_created_at=None, last_id=None)
    return Batch(
        rows=[_row_to_dict(row) for row in rows],
        last_created_at=rows[-1].created_at,
        last_id=rows[-1].id,
    )


def advance_cursor(session: Session, *, cursor: AuditExportCursor, batch: Batch) -> None:
    """Move the position, once the batch is somebody else's problem.

    Called only after a delivery the receiver accepted. Advancing first and
    posting after would turn one failed request into a permanent hole, and the
    hole would be invisible: the next run starts past the rows that never
    arrived.
    """
    if batch.is_empty:
        cursor.last_run_at = datetime.now(tz=UTC)
        session.commit()
        return
    cursor.last_created_at = batch.last_created_at
    cursor.last_id = batch.last_id
    cursor.rows_exported = int(cursor.rows_exported or 0) + len(batch.rows)
    cursor.last_run_at = datetime.now(tz=UTC)
    cursor.updated_at = datetime.now(tz=UTC)
    session.commit()


def build_body(batch: Batch, *, destination: str) -> dict[str, Any]:
    """The document the collector receives."""
    return {
        "version": BATCH_VERSION,
        "source": "trusca",
        "destination": destination,
        "count": len(batch.rows),
        "rows": batch.rows,
    }


def pending_count(session: Session, *, cursor: AuditExportCursor) -> int:
    """How far behind the export is. For the operator, not for the loop."""
    stmt = select(func.count()).select_from(AuditLog)
    if cursor.last_created_at is not None and cursor.last_id is not None:
        stmt = stmt.where(
            or_(
                AuditLog.created_at > cursor.last_created_at,
                and_(
                    AuditLog.created_at == cursor.last_created_at,
                    AuditLog.id > cursor.last_id,
                ),
            )
        )
    return int(session.execute(stmt).scalar_one())


def is_configured() -> bool:
    return audit_export_url() is not None


def purge_ready_count(
    session: Session,
    *,
    destination: str,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """How many audit rows are safe to purge by hand right now (W9).

    Read-only: it never touches ``audit_logs`` or the cursor row. A row
    counts only when it is BOTH already delivered to *destination* (at or
    before the cursor's ``(last_created_at, last_id)`` position, the same
    pair :func:`collect_batch` orders by) AND older than *retention_days*. No
    cursor yet (destination never configured, or configured but never run)
    means nothing has been exported, so nothing is ever counted. An
    unexported row is the one copy of that compliance record.

    ``audit_logs`` stays append-only at the database layer (migration 0012);
    this function feeds the manual, two-operator purge session documented at
    docs-site/docs/admin-guide/audit-log.md#retention, it does not replace it.
    """
    now = now or datetime.now(tz=UTC)
    cursor = session.execute(
        select(AuditExportCursor).where(AuditExportCursor.destination == destination)
    ).scalar_one_or_none()
    if cursor is None or cursor.last_created_at is None or cursor.last_id is None:
        return 0
    age_cutoff = now - timedelta(days=retention_days)
    stmt = (
        select(func.count())
        .select_from(AuditLog)
        .where(
            or_(
                AuditLog.created_at < cursor.last_created_at,
                and_(
                    AuditLog.created_at == cursor.last_created_at,
                    AuditLog.id <= cursor.last_id,
                ),
            ),
            AuditLog.created_at < age_cutoff,
        )
    )
    return int(session.execute(stmt).scalar_one())
