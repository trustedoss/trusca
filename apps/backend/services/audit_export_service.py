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

And the export never reads past a transaction that is still open. A row's
``created_at`` is ``now()``, which in PostgreSQL is the moment its transaction
began, not the moment it commits. A transaction that began earlier can
therefore commit later and land behind a position already passed; that row
would never be sent and nothing would say so. A fixed margin behind the
present only covers transactions shorter than the margin, and nothing bounds
how long a transaction runs (a retention sweep deleting thousands of scans is
one transaction). So each batch reads only rows stamped before the oldest
transaction still open on the database, and reads nothing at all while a
transaction is open whose start it cannot see. The rules and their limits are
in :func:`read_open_transactions` and :func:`collect_batch`.

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
from sqlalchemy import and_, func, or_, select, text, true, update
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
    """One batch of rows, where they end, and where they were read from.

    ``after_created_at`` / ``after_id`` is the position the batch was read
    after. :func:`advance_cursor` moves the cursor only if it is still there,
    so a batch read from a position somebody else has since moved cannot drag
    the cursor backwards or forwards over rows it never covered.
    """

    rows: list[dict[str, Any]]
    last_created_at: datetime | None
    last_id: uuid.UUID | None
    after_created_at: datetime | None = None
    after_id: uuid.UUID | None = None
    #: When the oldest open transaction the export could see began, if any.
    held_back_from: datetime | None = None
    #: The lag bound this batch was read under (database clock unless pinned).
    lag_horizon: datetime | None = None
    #: Open transactions whose start the export could not see. Any at all
    #: means nothing was read.
    hidden_transactions: int = 0
    #: Whether rows the lag alone would have admitted were left for later
    #: because of an open transaction. What decides whether to warn.
    rows_held_back: bool = False

    @property
    def held_back_seconds(self) -> float:
        """How much further the lag alone would have let the export read.

        Zero when the lag was the tighter bound, which is the normal case. A
        value that keeps growing run after run is a session left open
        somewhere, and the export cannot pass it until it ends.
        """
        if self.held_back_from is None or self.lag_horizon is None:
            return 0.0
        return max(0.0, (self.lag_horizon - self.held_back_from).total_seconds())

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


@dataclass(frozen=True)
class OpenTransactions:
    """What the database says is open, read just before a batch."""

    #: The database clock at the read, which stands in for "now".
    read_at: datetime
    #: Open transactions the export can see, by backend pid.
    starts: dict[int, datetime]
    #: Backends with a transaction open whose start the export cannot see.
    hidden: frozenset[int]
    #: ``track_activities``; off means no start time is published at all.
    tracking: bool

    @property
    def oldest(self) -> datetime | None:
        return min(self.starts.values(), default=None)


_READ_CLOCK_SQL = text(
    "SELECT statement_timestamp() AS read_at, "
    "current_setting('track_activities')::boolean AS tracking"
)

# Every transaction holds an exclusive lock on its own virtual transaction id
# for as long as it is open, and ``pg_locks`` shows that lock to any role.
# ``pg_stat_activity`` hides ``xact_start`` from a role that does not share the
# session's role, but still shows the row's database, pid and role id, so the
# two together say "a transaction is open here and its start is hidden".
_OPEN_TRANSACTIONS_SQL = text(
    "SELECT a.pid, a.xact_start "
    "FROM pg_stat_activity a "
    "LEFT JOIN (SELECT DISTINCT pid FROM pg_locks "
    "           WHERE locktype = 'virtualxid' AND mode = 'ExclusiveLock' "
    "           AND granted) v ON v.pid = a.pid "
    "WHERE a.datname = current_database() "
    "AND a.pid <> pg_backend_pid() "
    "AND a.pid NOT IN (SELECT pid FROM pg_stat_progress_vacuum) "
    "AND ((a.xact_start IS NOT NULL AND a.backend_type = 'client backend') "
    "     OR (a.xact_start IS NULL AND a.usesysid IS NOT NULL "
    "         AND v.pid IS NOT NULL))"
)


def read_open_transactions(session: Session) -> OpenTransactions:
    """The database's clock, and the transactions open on it.

    Any session that has a transaction open counts, including one that has
    only read so far: it can still write, and what it writes is stamped with
    its start time. The export's own session is left out because the export
    writes no audit rows. Background processes are left out when the export
    can see what they are (autovacuum, and the like) and so is a VACUUM,
    which cannot run inside a transaction that wrote anything else and is
    listed in ``pg_stat_progress_vacuum`` to every role.

    The clock is read in a statement before the one that reads the sessions,
    so it is earlier than anything that read could miss. A transaction that
    begins after the read is therefore dated after the clock, and the clock
    is what the lag is measured from. A worker clock running ahead of the
    database would otherwise place the horizon past such transactions.

    ``pg_stat_activity`` keeps one copy per transaction, taken the first time
    it is read, so the copy is discarded first; a stale one would miss a
    transaction that began since.

    A role without ``pg_read_all_stats`` is not shown ``xact_start`` for
    sessions of another role, which in a role-separated deployment includes
    the owner role and any superuser (the anonymisation script runs as the
    owner and writes an audit row). Those come back in ``hidden``; see
    :func:`collect_batch` for what is done with them. With
    ``track_activities`` off no start is published for anybody, so every open
    transaction lands there too: the export stays correct and reads only at
    moments when nothing is open.

    Two things this does not see. A prepared transaction (two-phase commit)
    has no session; the application never prepares one, and a deployment
    that does must not let it write audit rows. And the bound relies on the
    next statement taking a fresh snapshot, which is what READ COMMITTED
    (PostgreSQL's default and this application's) does: a transaction that
    commits after this read is then either visible to the read of
    ``audit_logs`` or was open here.
    """
    session.execute(text("SELECT pg_stat_clear_snapshot()"))
    clock = session.execute(_READ_CLOCK_SQL).one()
    starts: dict[int, datetime] = {}
    hidden: set[int] = set()
    for pid, xact_start in session.execute(_OPEN_TRANSACTIONS_SQL).all():
        if xact_start is None:
            hidden.add(int(pid))
        else:
            starts[int(pid)] = xact_start
    return OpenTransactions(
        read_at=clock.read_at,
        starts=starts,
        hidden=frozenset(hidden),
        tracking=bool(clock.tracking),
    )


def _after_cursor(cursor: AuditExportCursor) -> Any:
    """Strictly after the cursor's pair, or everything for a fresh cursor.

    The same timestamp is fine as long as the id is larger, which is what
    keeps a crowded millisecond from being either repeated or stepped over.
    """
    if cursor.last_created_at is None or cursor.last_id is None:
        return true()
    return or_(
        AuditLog.created_at > cursor.last_created_at,
        and_(
            AuditLog.created_at == cursor.last_created_at,
            AuditLog.id > cursor.last_id,
        ),
    )


def collect_batch(
    session: Session,
    *,
    cursor: AuditExportCursor,
    now: datetime | None = None,
) -> Batch:
    """Rows after the cursor that no open transaction can still write behind.

    Two bounds, both applied. Rows stamped strictly before the oldest open
    transaction began, which is the one the position depends on, and rows at
    least the configured lag old. ``now`` replaces the database clock for the
    lag bound only; the open transactions are always read from the database.

    While any transaction is open whose start the export cannot see, nothing
    is read and the position stays where it is. That is safe by induction:
    such a transaction was not open at the previous read, or the previous run
    would have read nothing either, so it began after that read, and every
    row it writes is dated after anything the previous run could have sent.
    The cost is a run that sends nothing while, say, the anonymisation script
    or a superuser's ``psql`` session holds a transaction open.

    Ordered by the same pair the position is stored as, so the end of one
    batch is the start of the next with nothing between them.
    """
    open_tx = read_open_transactions(session)
    if not open_tx.tracking:
        log.warning("audit_export_activity_tracking_off")
    lag_horizon = (now or open_tx.read_at) - timedelta(
        seconds=audit_export_lag_seconds()
    )
    oldest = open_tx.oldest
    after = _after_cursor(cursor)

    rows: list[AuditLog] = []
    if not open_tx.hidden:
        stmt = select(AuditLog).where(after, AuditLog.created_at <= lag_horizon)
        if oldest is not None:
            # Strictly before: a row stamped at exactly that instant may
            # belong to the open transaction itself.
            stmt = stmt.where(AuditLog.created_at < oldest)
        stmt = stmt.order_by(AuditLog.created_at.asc(), AuditLog.id.asc()).limit(
            audit_export_batch_size()
        )
        rows = list(session.execute(stmt).scalars().all())

    rows_held_back = False
    if open_tx.hidden or (oldest is not None and oldest <= lag_horizon):
        # Only asked when an open transaction was the tighter bound: is there
        # anything the lag alone would have let through? Without this, a
        # caught-up export would warn whenever a transaction older than the
        # lag happened to be open, with nothing waiting behind it.
        held = select(AuditLog.id).where(after, AuditLog.created_at <= lag_horizon)
        if not open_tx.hidden and oldest is not None:
            held = held.where(AuditLog.created_at >= oldest)
        rows_held_back = session.execute(held.limit(1)).first() is not None

    return Batch(
        rows=[_row_to_dict(row) for row in rows],
        last_created_at=rows[-1].created_at if rows else None,
        last_id=rows[-1].id if rows else None,
        after_created_at=cursor.last_created_at,
        after_id=cursor.last_id,
        held_back_from=oldest,
        lag_horizon=lag_horizon,
        hidden_transactions=len(open_tx.hidden),
        rows_held_back=rows_held_back,
    )


def advance_cursor(session: Session, *, cursor: AuditExportCursor, batch: Batch) -> bool:
    """Move the position, once the batch is somebody else's problem.

    Called only after a delivery the receiver accepted. Advancing first and
    posting after would turn one failed request into a permanent hole, and the
    hole would be invisible: the next run starts past the rows that never
    arrived.

    The move is conditional on the cursor still being where the batch was read
    from. The export task holds a lock for the whole run so two runs never
    work the same destination at once; this is the check that still holds if
    that lock is ever lost mid-run. Returns False, and leaves the row as it
    was, when the position had already moved. ``cursor`` is reloaded either
    way so the caller sees what the database holds.
    """
    now = datetime.now(tz=UTC)
    if batch.is_empty:
        cursor.last_run_at = now
        session.commit()
        return True
    result = session.execute(
        update(AuditExportCursor)
        .where(
            AuditExportCursor.id == cursor.id,
            AuditExportCursor.last_created_at.is_not_distinct_from(
                batch.after_created_at
            ),
            AuditExportCursor.last_id.is_not_distinct_from(batch.after_id),
        )
        .values(
            last_created_at=batch.last_created_at,
            last_id=batch.last_id,
            rows_exported=AuditExportCursor.rows_exported + len(batch.rows),
            last_run_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.refresh(cursor)
    moved = int(getattr(result, "rowcount", 0) or 0) == 1
    if not moved:
        log.warning(
            "audit_export_cursor_moved_underneath",
            cursor_id=str(cursor.id),
            rows=len(batch.rows),
        )
    return moved


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
