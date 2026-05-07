"""
Admin scan-queue HTTP routes — Phase 4 PR #14.

Endpoints under ``/v1/admin/scans``:
  - GET  /v1/admin/scans                   — paginated cross-team queue
  - POST /v1/admin/scans/{scan_id}/cancel  — force-cancel a running / queued scan

Auth: gated by the parent ``admin_router`` super-admin dependency.
Service-layer 4xx (404 not_found, 409 already_cancelled) translates to RFC
7807 Problem Details with snake_case extension fields.

Live progress updates flow through the existing WebSocket gateway
(``/ws/scans/{id}/progress``) — no admin-specific socket added here.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_super_admin_or_404
from schemas.admin_ops import (
    AdminScanListPage,
    ScanStatus,
)
from services.admin_scan_service import (
    AdminScanError,
    cancel_scan,
    list_scans,
)

router = APIRouter(prefix="/scans", tags=["admin"])
log = structlog.get_logger("admin.scans.api")


def _problem_for_admin_scan_error(request: Request, exc: AdminScanError) -> Response:
    """Translate an AdminScanError into an RFC 7807 response with extensions."""
    extensions: dict[str, object] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
        **extensions,
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/scans
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=AdminScanListPage,
    summary="List scans (admin) — cross-team queue with optional status filter",
)
async def list_scans_endpoint(
    request: Request,  # noqa: ARG001
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    # FastAPI parses the literal type into an enum-like Query param so a
    # value outside the closed set returns 422 + RFC 7807 automatically.
    status_filter: ScanStatus | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    page_obj = await list_scans(
        session,
        actor=actor,
        page=page,
        page_size=page_size,
        status=status_filter,
    )
    return Response(
        content=page_obj.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/scans/{scan_id}/cancel
# ---------------------------------------------------------------------------


@router.post(
    "/{scan_id}/cancel",
    summary="Force-cancel a scan (admin) — Celery revoke + status='cancelled'",
)
async def cancel_scan_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        item = await cancel_scan(session, actor=actor, scan_id=scan_id)
    except AdminScanError as exc:
        return _problem_for_admin_scan_error(request, exc)

    log.warning(
        "admin.scan.cancel",
        actor_id=str(actor.id),
        scan_id=str(scan_id),
    )
    return Response(
        content=item.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
