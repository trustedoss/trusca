"""
Report-download history service — W3 #32a-2 (Reports center, emit + list half).

Two responsibilities:

1. :func:`record_report_download` — append a single ``report_downloads`` row.
   Called from the four read-only download endpoints (NOTICE / SBOM / vuln
   PDF / VEX export) AFTER the response body has been built and JUST BEFORE
   the response is returned. The helper is **best-effort**: any DB error is
   logged with full traceback and **swallowed**, because the download itself
   has already succeeded and the user must not see a 5xx for a missed audit
   row. CLAUDE.md §5 also routes the user-agent string through ``mask_pii``
   before it reaches the column — defensive: a UA cannot legitimately carry
   credentials, but the masking helper is the policy-level filter.

2. :func:`list_report_history` — paginated read for the Reports tab. The
   cross-team / existence-hide contract (404, never 403) matches every other
   project-scoped read in this service (SBOM / NOTICE / source-tree). The
   query is ordered by ``created_at DESC`` so the project's
   ``(project_id, created_at DESC)`` compound index serves it directly.

Tenancy
-------
The model denormalises ``team_id`` from the parent project at insert time so
admin / team-wide queries can filter by tenant without joining ``projects``.
The emit helper mirrors that column from ``project.team_id``; the list helper
trusts the parent project's ``team_id`` for the cross-team gate (a value race
between the two would be a separate bug).

Why not the SQLAlchemy audit listener
-------------------------------------
``audit_logs`` is driven by ``before_flush`` and only fires on INSERT / UPDATE /
DELETE on tracked tables. The four download endpoints are pure read paths —
they make no flush, so they would be silently absent from the audit trail. A
dedicated emit table with explicit INSERTs is the forward-compatible answer.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, cast

import structlog
from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from core.authz import assert_team_access
from core.pii_mask import mask_pii
from core.security import CurrentUser
from models import Project, ReportDownload, User
from models.report_download import REPORT_TYPE_VALUES
from schemas.report_download import (
    ReportDownloadEntry,
    ReportDownloadUserSummary,
    ReportHistoryResponse,
)
from services.project_service import ProjectNotFound

log = structlog.get_logger("report_download.service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# CLAUDE.md §5 — defensive bound on UA length. The DB column is VARCHAR(512)
# so anything longer would already raise on INSERT; we truncate at emit time
# so a hostile client cannot trigger a 5xx by sending a multi-MB UA header.
_USER_AGENT_MAX = 512

# Page-size cap mirrors the audit search surface (admin_ops.AuditSearchQuery).
PAGE_SIZE_MIN = 1
PAGE_SIZE_MAX = 200
PAGE_SIZE_DEFAULT = 50


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ReportHistoryError(Exception):
    """Base class for report-history list errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Report History Error"


class ReportHistoryNotFound(ReportHistoryError):
    """Project does not exist, or the caller is not a member of its team.

    Existence-hide: the same envelope is used for "no such project_id" and
    "exists but cross-team" so a non-member cannot enumerate projects.
    """

    status_code = 404
    title = "Project Not Found"


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------


def _truncate_user_agent(raw: str | None) -> str | None:
    if raw is None:
        return None
    # mask_pii() preserves a plain string verbatim (PII tokens only match dict
    # keys), but we still route it through the helper so a future policy change
    # (e.g. redaction of bearer-looking substrings) lights up automatically.
    masked = mask_pii(raw)
    if not isinstance(masked, str):  # pragma: no cover - defensive
        return None
    return masked[:_USER_AGENT_MAX]


def _client_ip_from(request: Request | None) -> str | None:
    """Best-effort client IP, mirroring ``core.middleware._extract_client_ip``.

    Audit middleware already binds the (XFF-aware) IP into the audit context,
    but we read directly off the request here so the helper stays independent
    of middleware install order. The XFF preference matches the limiter +
    audit so a single deployment never sees three different IPs for the same
    hop.
    """
    if request is None:
        return None
    xff = request.headers.get("x-forwarded-for")
    if xff:
        first = xff.split(",", 1)[0].strip()
        if first:
            return first
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return str(host) if host else None


async def record_report_download(
    session: AsyncSession,
    *,
    project: Project,
    scan_id: uuid.UUID | None,
    user: CurrentUser | User | None,
    report_type: str,
    fmt: str,
    size_bytes: int | None,
    request: Request | None,
) -> None:
    """Append one ``report_downloads`` row for a successful export.

    Best-effort: ANY DB error is logged + swallowed. The download has already
    succeeded; surfacing a 5xx because the audit row failed to INSERT would be
    a worse user experience than missing a history line. Operators can spot
    misses via the ``report_downloads.emit_failed`` log event.

    No explicit ``await session.commit()`` — the caller's request-scoped
    session commits on dependency exit (FastAPI ``get_db`` contract). Calling
    commit here would clobber any in-progress transaction the endpoint owns.
    """
    if report_type not in REPORT_TYPE_VALUES:
        # Programmer error in the call site, not a user-driven event. Log it
        # rather than raise so we still don't 5xx a successful download.
        log.error(
            "report_downloads.emit_failed",
            reason="unknown_report_type",
            report_type=report_type,
            project_id=str(project.id),
        )
        return

    user_id = user.id if user is not None else None

    # Pre-stringify identifiers BEFORE the try block so the exception path
    # never has to touch ORM attributes. After a failed commit the session
    # transaction is DEACTIVE; touching a Mapped attribute (e.g. ``project.id``)
    # in the except branch would trigger a re-load and surface
    # ``PendingRollbackError`` — the failed commit's wrapper — instead of the
    # SQLAlchemyError we want to log + swallow. Capturing the strings up front
    # keeps the swallow path attribute-free and fully decoupled from the
    # session's transactional state.
    project_id_str = str(project.id)
    team_id_value = project.team_id
    user_agent_value = _truncate_user_agent(
        request.headers.get("user-agent") if request is not None else None,
    )
    client_ip_value = _client_ip_from(request)
    scan_id_str = str(scan_id) if scan_id is not None else None
    user_id_str = str(user_id) if user_id is not None else None

    try:
        row = ReportDownload(
            project_id=project.id,
            scan_id=scan_id,
            team_id=team_id_value,
            user_id=user_id,
            report_type=report_type,
            format=fmt,
            size_bytes=size_bytes,
            client_ip=client_ip_value,
            user_agent=user_agent_value,
        )
        session.add(row)
        # Commit explicitly: the read-only download endpoints that call this
        # helper do not otherwise mutate state, and the FastAPI ``get_db``
        # dependency closes the session without auto-commit, so a missing
        # commit would silently drop the row on session close. The commit is
        # safe to issue here because the surrounding endpoint is a pure read
        # — there is no other in-flight write to clobber. If the surrounding
        # endpoint ever grows mutations, the commit semantics will still be
        # correct (commit at this point persists both the mutation and this
        # row; any later failure rolls back nothing already-committed).
        await session.commit()
    except SQLAlchemyError:
        # The download already succeeded; do NOT propagate. structlog records
        # the failure with the stack so operators can investigate. Roll back
        # the deactivated transaction so the session is reusable for any
        # subsequent (unrelated) writes in this request — though the four
        # current call sites are read-only, the helper must remain safe to
        # invoke from a future write endpoint.
        try:
            await session.rollback()
        except SQLAlchemyError:  # pragma: no cover - defensive
            pass
        log.error(
            "report_downloads.emit_failed",
            exc_info=True,
            report_type=report_type,
            project_id=project_id_str,
            scan_id=scan_id_str,
            user_id=user_id_str,
        )


# ---------------------------------------------------------------------------
# History list
# ---------------------------------------------------------------------------


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    """Project lookup that raises :class:`ReportHistoryNotFound` on miss.

    The existence-hide policy folds "missing" and "cross-team" into one 404,
    so we surface the same exception either way. Cross-team is enforced by
    the caller via :func:`core.authz.assert_team_access`.
    """
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise ReportHistoryNotFound(f"project {project_id} not found")
    return project


async def list_report_history(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    viewer: CurrentUser,
    type_filter: Sequence[str] | None = None,
    scan_id_filter: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = PAGE_SIZE_DEFAULT,
) -> ReportHistoryResponse:
    """Return the paginated download history for one project.

    Cross-team / unknown-project both raise :class:`ReportHistoryNotFound`
    (404 existence-hide). super_admin bypasses the team check; otherwise the
    project's ``team_id`` must be in ``viewer.team_ids``.

    Sort is ``created_at DESC`` so the
    ``ix_report_downloads_project_created_at (project_id, created_at DESC)``
    index serves the query without an extra sort step.
    """
    if page < 1:
        # Defensive — the router's Query() already enforces ge=1 + 422, but a
        # service-layer guard means unit tests can call this directly without
        # going through FastAPI.
        raise ReportHistoryError("page must be >= 1")
    if page_size < PAGE_SIZE_MIN or page_size > PAGE_SIZE_MAX:
        raise ReportHistoryError(
            f"page_size must be between {PAGE_SIZE_MIN} and {PAGE_SIZE_MAX}",
        )

    project = await _load_project(session, project_id)

    # Existence-hide: cross-team surfaces the same 404 the unknown-project
    # branch returns. The helper emits ``authz.cross_team_attempt`` before
    # raising, so SOC tooling sees the rejection regardless of the HTTP shape.
    assert_team_access(
        viewer,
        project.team_id,
        log=log,
        resource="report_history",
        resource_id=str(project_id),
        deny=lambda: ReportHistoryNotFound(f"project {project_id} not found"),
    )

    # Validate type filter inputs — the router constrains them via the wire
    # enum too, but a service-layer caller (or a future internal lister) must
    # still reject garbage.
    validated_types: list[str] = []
    if type_filter:
        for token in type_filter:
            if token not in REPORT_TYPE_VALUES:
                raise ReportHistoryError(f"unknown report_type: {token!r}")
            if token not in validated_types:
                validated_types.append(token)

    # User join is OUTER so anonymized rows (user_id IS NULL after a user
    # delete) still appear in the response with user=None.
    user_alias = aliased(User)
    base = (
        select(ReportDownload, user_alias)
        .outerjoin(user_alias, user_alias.id == ReportDownload.user_id)
        .where(ReportDownload.project_id == project_id)
    )
    if validated_types:
        base = base.where(ReportDownload.report_type.in_(validated_types))
    if scan_id_filter is not None:
        base = base.where(ReportDownload.scan_id == scan_id_filter)

    # Total — run as a separate count() so the row query can paginate without
    # carrying the full count. Postgres can use the same compound index for
    # both because the filter predicates are leftmost-prefix.
    count_stmt = select(func.count()).select_from(
        base.with_only_columns(ReportDownload.id).order_by(None).subquery(),
    )
    total = int((await session.execute(count_stmt)).scalar_one())

    rows_stmt = (
        base.order_by(ReportDownload.created_at.desc(), ReportDownload.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(rows_stmt)).all()

    items: list[ReportDownloadEntry] = []
    for row in rows:
        download = cast(ReportDownload, row[0])
        user_row = cast(User | None, row[1])
        user_summary: ReportDownloadUserSummary | None = None
        if user_row is not None:
            user_summary = ReportDownloadUserSummary(
                id=user_row.id,
                email=user_row.email,
            )
        items.append(
            ReportDownloadEntry(
                id=download.id,
                project_id=download.project_id,
                scan_id=download.scan_id,
                team_id=download.team_id,
                user=user_summary,
                report_type=cast(Any, download.report_type),
                format=download.format,
                size_bytes=download.size_bytes,
                created_at=download.created_at,
            )
        )

    return ReportHistoryResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


__all__ = [
    "PAGE_SIZE_DEFAULT",
    "PAGE_SIZE_MAX",
    "PAGE_SIZE_MIN",
    "ReportHistoryError",
    "ReportHistoryNotFound",
    "list_report_history",
    "record_report_download",
]


# ---------------------------------------------------------------------------
# Public re-exports for adjacent modules
# ---------------------------------------------------------------------------

# Re-import ProjectNotFound so callers can ``except (ReportHistoryNotFound,
# ProjectNotFound)`` in the router error handler without needing two imports.
# Pyflakes happy: explicit __all__ above already documents the public surface.
_ = ProjectNotFound
