"""
Admin audit-log search + CSV export service — Phase 4 PR #14.

Surfaces the immutable ``audit_logs`` table to super-admin operators with:

  - :func:`search_audit`     — paginated JSON results for the admin UI.
  - :func:`stream_audit_csv` — async iterator yielding CSV rows for export.

Filtering surface (mirror of ``schemas.admin_ops.AuditSearchQuery``):
  - ``actor_user_id`` — UUID equality.
  - ``target_table`` — closed Literal whitelist (rejected at the boundary).
  - ``action``       — bounded substring; the SQL layer uses ``ILIKE`` with
                        a parameterized argument.
  - ``from`` / ``to`` — inclusive timestamp range on ``created_at``.
  - ``q``            — free-text JSONB search across ``diff``. We render
                        ``diff::text ILIKE :pattern`` because GIN-indexed
                        ``@>`` containment is not a substring match. Bound
                        parameters keep this safe against injection.
  - ``page`` / ``page_size`` — keyset pagination is overkill for an audit
                                table that grows linearly; OFFSET is fine
                                up to a few hundred thousand rows.

CSV export caps:
  - 100 000 rows hard ceiling. Anything larger raises 413
    ``audit_export_too_large`` so an operator does not accidentally dump
    a year of audit records into a browser download.
  - Streaming uses ``yield`` chunks of N rows so a five-figure export does
    not buffer in memory.
  - ``diff`` is intentionally NOT in the CSV — JSONB shapes vary across
    target tables and the data may contain low-PII fragments that the
    listener could not mask. The web UI shows the diff inline; the export
    is for time / actor / action / table forensics.

Adversarial input note (memory ``feedback_adversarial_input_parametrize``):
  Every variable that reaches SQL goes through SQLAlchemy bound parameters
  (``select(...).where(...)``). The schema layer rejects null bytes / CR /
  LF on free-text inputs. Combined, an attacker has no SQL-injection
  surface here even with a maliciously crafted ``q`` or ``action``.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import TEXT
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import AuditLog, User
from schemas.admin_ops import (
    AuditLogItem,
    AuditLogListPage,
    AuditSearchQuery,
)

log = structlog.get_logger("admin.audit.service")

# Hard cap on a single CSV export. Beyond this the admin should narrow the
# time window. 100k rows × ~200 bytes/row ≈ 20 MB raw — well within a
# browser-friendly ceiling but enough that the operator gets explicit
# feedback before kicking off a multi-minute query.
_CSV_EXPORT_HARD_LIMIT = 100_000

# Streaming chunk size for the CSV iterator. Chosen so the buffer the
# StreamingResponse holds at any moment stays under ~200 KB.
_CSV_STREAM_CHUNK_ROWS = 1_000


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AdminAuditError(Exception):
    """Base class for admin audit errors mapped to RFC 7807."""

    status_code: int = 400
    title: str = "Admin Audit Error"
    type_uri: str = "about:blank"
    extensions: dict[str, object] = {}


class AuditExportTooLarge(AdminAuditError):
    status_code = 413
    title = "Audit Export Too Large"
    type_uri = "https://docs.trustedoss.io/errors/audit-export-too-large"
    extensions = {"audit_export_too_large": True}


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _apply_filters(stmt: Any, *, query: AuditSearchQuery) -> Any:
    """Apply the typed filters to a base ``select(AuditLog)`` (or count) statement.

    Centralized so the search and export paths stay in lockstep: the export
    must respect every filter the search sees.
    """
    if query.actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == query.actor_user_id)
    if query.target_table is not None:
        stmt = stmt.where(AuditLog.target_table == query.target_table)
    if query.action:
        # ILIKE substring match — the schema validator already rejected
        # null / CR / LF, and bound parameters keep this injection-safe.
        # ``%`` and ``_`` in the input become wildcards; we accept that
        # behaviour as feature, not bug, for an admin search box.
        stmt = stmt.where(AuditLog.action.ilike(f"%{query.action}%"))
    if query.from_ is not None:
        stmt = stmt.where(AuditLog.created_at >= query.from_)
    if query.to is not None:
        stmt = stmt.where(AuditLog.created_at <= query.to)
    if query.q:
        # JSONB free-text — cast diff to text so substring search works.
        # Postgres can use the GIN index for containment but not for ILIKE,
        # so this falls back to a sequential scan on the rows that already
        # matched the other filters. That is fine: actor / target_table /
        # time-range usually narrow to thousands of rows, and ILIKE on
        # JSONB-as-text is fast at that scale.
        #
        # G5: escape LIKE metacharacters in q so a superadmin cannot trigger
        # pathological queries with patterns like "%%" or "%%%%...".
        # We use the standard SQL escape character '\'.
        escaped_q = query.q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            cast(AuditLog.diff, TEXT).ilike(f"%{escaped_q}%", escape="\\")
        )
    return stmt


# ---------------------------------------------------------------------------
# search_audit
# ---------------------------------------------------------------------------


async def search_audit(
    session: AsyncSession,
    *,
    actor: CurrentUser,  # noqa: ARG001 — kept for symmetry with other admin services
    query: AuditSearchQuery,
) -> AuditLogListPage:
    """
    Return a page of audit rows matching the filters.

    The actor email is resolved with a LEFT JOIN against ``users`` so a row
    whose ``actor_user_id`` was nulled out (FK ``ondelete='SET NULL'``)
    still renders cleanly with ``actor_email = None``.
    """
    page = max(query.page, 1)
    page_size = max(min(query.page_size, 200), 1)

    # Count first so the page envelope can carry the total without a second
    # query against the user join (which returns the same row set; the
    # COUNT version drops the join to avoid unnecessary work).
    count_stmt = _apply_filters(
        select(func.count()).select_from(AuditLog),
        query=query,
    )
    total = int((await session.execute(count_stmt)).scalar_one())

    base = _apply_filters(
        select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_user_id),
        query=query,
    )
    rows_stmt = (
        base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await session.execute(rows_stmt)).all()

    items = [
        AuditLogItem(
            id=row[0].id,
            created_at=row[0].created_at,
            actor_user_id=row[0].actor_user_id,
            actor_email=row[1],
            team_id=row[0].team_id,
            target_table=row[0].target_table,
            target_id=row[0].target_id,
            action=row[0].action,
            request_id=row[0].request_id,
            diff=row[0].diff,
        )
        for row in rows
    ]

    has_more = page * page_size < total
    return AuditLogListPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_more=has_more,
    )


# ---------------------------------------------------------------------------
# stream_audit_csv
# ---------------------------------------------------------------------------


# Column order is the contract — the admin UI's "open in spreadsheet"
# button assumes this header line. Any change here is API-breaking.
_CSV_COLUMNS = (
    "created_at",
    "actor_user_id",
    "actor_email",
    "team_id",
    "target_table",
    "target_id",
    "action",
    "request_id",
)


async def stream_audit_csv(
    session: AsyncSession,
    *,
    actor: CurrentUser,  # noqa: ARG001
    query: AuditSearchQuery,
) -> AsyncIterator[str]:
    """
    Yield CSV rows for the matching audit slice.

    First yields the header line, then chunked rows. Raises
    :class:`AuditExportTooLarge` (413) when the count would exceed the
    hard limit — checked BEFORE streaming so the operator gets an
    actionable error instead of a partial download.

    No ``diff`` column: JSONB shapes vary, the listener masks credentials
    but other domain fields could leak more than the operator expects in
    a flat CSV. The UI keeps the JSON view for forensics; the CSV is for
    time / actor / action / table-level trail.
    """
    count_stmt = _apply_filters(
        select(func.count()).select_from(AuditLog),
        query=query,
    )
    total = int((await session.execute(count_stmt)).scalar_one())

    if total > _CSV_EXPORT_HARD_LIMIT:
        raise AuditExportTooLarge(
            f"audit export would return {total} rows (limit {_CSV_EXPORT_HARD_LIMIT}); "
            "narrow the time window or filters and retry"
        )

    # Header — flush a single line so consumers like spreadsheets can detect
    # column structure on the first chunk.
    #
    # UTF-8 byte-order mark prefix (A3, walkthrough sys-bug-audit-2): Excel
    # on Korean / Japanese / Chinese locales requires a BOM to detect UTF-8
    # automatically; without it, double-clicking the CSV opens it under the
    # locale's legacy code page (CP949 / SJIS / GB18030) and every non-ASCII
    # actor email or audit row diff renders as mojibake. The BOM is a single
    # 3-byte (\xef\xbb\xbf) prefix on the first chunk, so the wire size cost
    # is one-time and tools that already auto-detect UTF-8 (LibreOffice, awk,
    # python's csv module) silently strip it.
    yield "\ufeff" + _csv_line(_CSV_COLUMNS)

    base = _apply_filters(
        select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.actor_user_id),
        query=query,
    )
    base = base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())

    # OFFSET / LIMIT chunking. SQLAlchemy's async session does not expose
    # server-side cursors trivially for asyncpg, so we page through. With
    # the chunk size constant + our hard cap the worst case is 100 chunks.
    offset = 0
    while offset < total:
        chunk_stmt = base.limit(_CSV_STREAM_CHUNK_ROWS).offset(offset)
        rows = (await session.execute(chunk_stmt)).all()
        if not rows:
            break
        for row in rows:
            yield _csv_line(_format_row(row[0], row[1]))
        offset += len(rows)


def _csv_line(values: tuple[Any, ...] | list[Any]) -> str:
    """Render a single CSV row including the trailing newline."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow([_csv_cell(v) for v in values])
    return buffer.getvalue()


_DANGEROUS_CSV_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: Any) -> str:
    """Convert a column value to a CSV-safe string.

    - ``None`` → empty cell.
    - ``datetime`` → ISO-8601 with timezone.
    - ``UUID`` → canonical hyphenated form.
    - leading ``= + - @ \\t \\r`` → prepended with ``'`` per OWASP CSV-injection
      cheat-sheet. Without this, an audit row whose ``action`` / ``target_id``
      / ``request_id`` (any operator-controlled column) starts with ``=`` is
      executed as a formula when the export is opened in Excel / LibreOffice /
      Sheets, giving the attacker DDE / shell-escape against the super-admin's
      workstation (CWE-1236).
    - everything else → ``str(value)``.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    rendered = str(value)
    if rendered and rendered[0] in _DANGEROUS_CSV_PREFIX:
        return "'" + rendered
    return rendered


def _format_row(row: AuditLog, actor_email: str | None) -> tuple[Any, ...]:
    """Project an AuditLog ORM row into the CSV column tuple."""
    return (
        row.created_at,
        row.actor_user_id,
        actor_email,
        row.team_id,
        row.target_table,
        row.target_id,
        row.action,
        row.request_id,
    )


__all__ = [
    "AdminAuditError",
    "AuditExportTooLarge",
    "search_audit",
    "stream_audit_csv",
]
