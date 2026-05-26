"""
Scan read API — Phase 2 PR #7 + Step 4 (cross-project listing).

Endpoints under `/v1`:
  - GET /v1/scans                            List scans across every project
                                              the actor can see (Step 4).
  - GET /v1/scans/{scan_id}                  Read one scan (IDOR-safe via
                                              team membership on the parent
                                              project).
  - GET /v1/projects/{project_id}/scans      List scans for a project.

The scan trigger (POST) lives in `api/v1/projects.py` because it is naturally
a sub-resource of a project. The read endpoints sit here because clients
fetch them by scan id (notification deep links, audit log entries) without
necessarily knowing the parent project up front.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.scan import ScanListResponse, ScanPublic
from services.admin_scan_service import (
    AdminScanError,
    cancel_scan_for_actor,
)
from services.scan_service import (
    ScanError,
    get_scan,
    list_scans_for_actor,
    list_scans_for_project,
)

router = APIRouter(prefix="/v1", tags=["scans"])
log = structlog.get_logger("scans.api")


def _problem_for_scan_error(request: Request, exc: ScanError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_admin_scan_error(request: Request, exc: AdminScanError) -> Response:
    """Translate an AdminScanError (shared cancel core) into RFC 7807.

    Mirrors the admin endpoint's translator so the user-facing cancel returns
    the identical envelope (incl. ``scan_already_cancelled`` extension) on a
    409 — the only difference between the two surfaces is the team-access gate.
    """
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
# GET /v1/scans  (cross-project list — Step 4)
# ---------------------------------------------------------------------------


@router.get(
    "/scans",
    response_model=ScanListResponse,
    summary="List scans across every project accessible to the caller",
)
async def list_my_scans_endpoint(
    request: Request,
    status_filter: str | None = Query(
        default=None,
        alias="status",
        # Mirror SCAN_STATUS_VALUES from models.scan. Pydantic emits 422 for
        # any other value with an RFC 7807 envelope (the validation handler
        # in core.errors).
        pattern=r"^(queued|running|succeeded|failed|cancelled)$",
        description="Filter by scan status.",
    ),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    rows, total = await list_scans_for_actor(
        session,
        actor=actor,
        status_filter=status_filter,
        page=page,
        size=size,
    )
    body = ScanListResponse(
        items=[ScanPublic.from_scan(s) for s in rows],
        total=total,
        page=page,
        size=size,
    )
    return Response(
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/scans/{scan_id}
# ---------------------------------------------------------------------------


@router.get(
    "/scans/{scan_id}",
    response_model=ScanPublic,
    summary="Read one scan (IDOR-safe via project team membership)",
)
async def get_scan_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        scan = await get_scan(session, scan_id=scan_id, actor=actor)
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    body = ScanPublic.model_validate(scan)
    return Response(
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/scans/{scan_id}/cancel  (user-facing — own-team scans only)
# ---------------------------------------------------------------------------


@router.post(
    "/scans/{scan_id}/cancel",
    summary="Cancel a queued / running scan owned by the caller's team",
)
async def cancel_scan_endpoint(
    request: Request,
    scan_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    """Cancel one of the caller's own team's scans.

    PR-A1 (scan stability). Auth: any authenticated team member
    (``developer`` or higher). The owning-team check lives in the service
    (``cancel_scan_for_actor``) which existence-hides other teams' scans as
    404 — so a developer cannot probe scan ids belonging to other teams.

    Admin force-cancel (``POST /v1/admin/scans/{id}/cancel``) remains separate
    and cross-team; the two share the same revoke + status-mutation core.
    """
    try:
        item = await cancel_scan_for_actor(session, actor=actor, scan_id=scan_id)
    except AdminScanError as exc:
        return _problem_for_admin_scan_error(request, exc)

    log.warning(
        "scan.cancel",
        actor_id=str(actor.id),
        scan_id=str(scan_id),
    )
    return Response(
        content=item.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/scans
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/scans",
    response_model=ScanListResponse,
    summary="List scans for a project (most recent first)",
)
async def list_scans_endpoint(
    request: Request,
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_scans_for_project(
            session,
            project_id=project_id,
            actor=actor,
            page=page,
            size=size,
        )
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    body = ScanListResponse(
        items=[ScanPublic.from_scan(s) for s in rows],
        total=total,
        page=page,
        size=size,
    )
    return Response(
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
