"""
Admin audit-log HTTP routes — Phase 4 PR #14.

Endpoints under ``/v1/admin/audit``:
  - GET /v1/admin/audit             — paginated JSON search
  - GET /v1/admin/audit/export.csv  — same filters, streaming CSV download

Auth: gated by the parent ``admin_router`` super-admin dependency.

Filtering surface mirrors :class:`schemas.admin_ops.AuditSearchQuery`:
  ``actor_user_id`` / ``target_table`` / ``action`` / ``from`` / ``to`` /
  ``q``. Closed enums are validated by Pydantic Literals; null-byte
  injection is rejected by the schema's field validators.

CSV export streaming:
  - The service hard-caps the result set at 100 000 rows (413 +
    ``audit_export_too_large``) so a runaway export does not exhaust the
    backend's memory.
  - ``Content-Type: text/csv; charset=utf-8`` and a
    ``Content-Disposition: attachment; filename=...`` header. The filename
    embeds the ``from`` / ``to`` window (or ``all`` when unbounded) so a
    series of exports stays distinguishable on disk.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import (
    AuditLogListPage,
    AuditSearchQuery,
    AuditTargetTable,
)
from services.admin_audit_service import (
    AdminAuditError,
    search_audit,
    stream_audit_csv,
)

router = APIRouter(prefix="/audit", tags=["admin"])
log = structlog.get_logger("admin.audit.api")


def _problem_for_admin_audit_error(request: Request, exc: AdminAuditError) -> Response:
    """Translate an AdminAuditError into an RFC 7807 response with extensions."""
    extensions: dict[str, object] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
        **extensions,
    )


def _build_query(
    *,
    actor_user_id: uuid.UUID | None,
    target_table: AuditTargetTable | None,
    action: str | None,
    from_: datetime | None,
    to: datetime | None,
    q: str | None,
    page: int,
    page_size: int,
) -> AuditSearchQuery:
    """Materialize the query model from the route's Query params.

    Centralized so the JSON and CSV endpoints share the validation +
    construction. The model's field validators run on assignment, so an
    invalid ``q`` / ``action`` is rejected here as a 422 before reaching
    the service layer.
    """
    # ``from`` is a Python keyword; the model uses ``from_`` (alias ``from``)
    # so we instantiate via ``model_validate`` with the alias key so the
    # validator chain runs identically to a JSON-driven construction.
    return AuditSearchQuery.model_validate(
        {
            "actor_user_id": actor_user_id,
            "target_table": target_table,
            "action": action,
            "from": from_,
            "to": to,
            "q": q,
            "page": page,
            "page_size": page_size,
        }
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/audit
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=AuditLogListPage,
    summary="Search audit log (admin) — paginated, filterable",
)
async def search_audit_endpoint(
    request: Request,  # noqa: ARG001
    actor_user_id: uuid.UUID | None = Query(default=None),
    target_table: AuditTargetTable | None = Query(default=None),
    action: str | None = Query(default=None, max_length=64),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    query = _build_query(
        actor_user_id=actor_user_id,
        target_table=target_table,
        action=action,
        from_=from_,
        to=to,
        q=q,
        page=page,
        page_size=page_size,
    )
    page_obj = await search_audit(session, actor=actor, query=query)
    return Response(
        content=page_obj.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/audit/export.csv
# ---------------------------------------------------------------------------


def _filename_for(*, from_: datetime | None, to: datetime | None) -> str:
    """Generate a self-describing CSV filename."""

    def _stamp(d: datetime | None) -> str:
        return d.strftime("%Y%m%d") if d is not None else "all"

    return f"audit_export_{_stamp(from_)}_{_stamp(to)}.csv"


@router.get(
    "/export.csv",
    summary="Export audit log to CSV (admin) — streaming, capped at 100k rows",
    response_class=StreamingResponse,
)
async def export_audit_csv_endpoint(
    request: Request,
    actor_user_id: uuid.UUID | None = Query(default=None),
    target_table: AuditTargetTable | None = Query(default=None),
    action: str | None = Query(default=None, max_length=64),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
    q: str | None = Query(default=None, max_length=255),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    query = _build_query(
        actor_user_id=actor_user_id,
        target_table=target_table,
        action=action,
        from_=from_,
        to=to,
        q=q,
        page=1,
        page_size=200,  # ignored by stream path
    )

    # The service raises AuditExportTooLarge BEFORE yielding anything. We
    # have to peek at the count up-front to surface that as RFC 7807;
    # otherwise the response would return 200 + a partial CSV, which is
    # the wrong UX for a runaway export.
    try:
        # Drain the iterator into a generator that we can wrap with
        # StreamingResponse. The first request to the iterator triggers the
        # count query inside the service; if that raises, we catch + map.
        iterator = stream_audit_csv(session, actor=actor, query=query)
        # Materialize the first chunk so AuditExportTooLarge surfaces here
        # as an HTTP error rather than mid-download.
        first = await iterator.__anext__()
    except AdminAuditError as exc:
        return _problem_for_admin_audit_error(request, exc)
    except StopAsyncIteration:
        # Empty result set — return an empty CSV with just the header
        # (already yielded as ``first`` above unless StopAsyncIteration
        # fires immediately, which only happens if the iterator was empty
        # AFTER the header — impossible by construction). Fall through
        # below; ``first`` is bound to the header line.
        first = ""

    async def _gen() -> AsyncIterator[str]:
        if first:
            yield first
        async for chunk in iterator:
            yield chunk

    headers = {
        "Content-Disposition": f'attachment; filename="{_filename_for(from_=from_, to=to)}"',
    }
    log.info(
        "admin.audit.csv_export_started",
        actor_id=str(actor.id),
        actor_user_id=str(actor_user_id) if actor_user_id else None,
        target_table=target_table,
    )
    return StreamingResponse(
        _gen(),
        media_type="text/csv; charset=utf-8",
        headers=headers,
        status_code=status.HTTP_200_OK,
    )


__all__ = ["router"]
