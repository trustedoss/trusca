"""
Project CRUD + scan trigger API — Phase 2 PR #7.

Endpoints under `/v1/projects`:
  - POST   /v1/projects                    Create a project (role >= developer
                                            within target team).
  - GET    /v1/projects                    List projects visible to caller
                                            (paginated; team_id-clamped for
                                            non-super-admins).
  - GET    /v1/projects/{project_id}       Read one project (IDOR-safe).
  - PATCH  /v1/projects/{project_id}       Update mutable fields (role >=
                                            team_admin within project's team).
  - DELETE /v1/projects/{project_id}       Soft-delete (archive) the project.
  - POST   /v1/projects/{project_id}/scans Trigger a scan (skeleton — PR #7
                                            persists the row only; Celery
                                            enqueue lands in PR #8).

All 4xx/5xx responses are RFC 7807 problem+json. Domain exceptions raised by
the service layer (`services/project_service.py`,
`services/scan_service.py`) are translated to status codes here so the
service layer never leaks into the wire format.

Auth: every route requires a valid access token. The `require_role(...)`
dependency factory enforces minimum role (developer for read/create;
team_admin for update/archive). Cross-team data access (IDOR) is enforced
inside the service: this router does NOT decide who can read what — it only
decides who is authenticated.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.scan import (
    ProjectCreate,
    ProjectListResponse,
    ProjectPublic,
    ProjectUpdate,
    ScanCreate,
    ScanPublic,
)
from services.project_service import (
    ProjectError,
    archive_project,
    create_project,
    get_project,
    list_projects,
    update_project,
)
from services.scan_service import (
    ScanError,
    trigger_scan,
)

router = APIRouter(prefix="/v1/projects", tags=["projects"])
log = structlog.get_logger("projects.api")


# ---------------------------------------------------------------------------
# Error translation helpers
# ---------------------------------------------------------------------------


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_scan_error(request: Request, exc: ScanError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# POST /v1/projects
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ProjectPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project (auth required, role >= developer)",
)
async def create_project_endpoint(
    request: Request,
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        project = await create_project(session, payload=payload, actor=actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectPublic.model_validate(project)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List projects visible to the caller",
)
async def list_projects_endpoint(
    request: Request,
    team_id: uuid.UUID | None = Query(default=None),
    include_archived: bool = Query(default=False),
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_projects(
            session,
            actor=actor,
            team_id=team_id,
            include_archived=include_archived,
            q=q,
            page=page,
            size=size,
        )
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectListResponse(
        items=[ProjectPublic.model_validate(p) for p in rows],
        total=total,
        page=page,
        size=size,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}
# ---------------------------------------------------------------------------


@router.get(
    "/{project_id}",
    response_model=ProjectPublic,
    summary="Read one project (IDOR-safe; 403 if not a team member)",
)
async def get_project_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectPublic.model_validate(project)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/projects/{project_id}
# ---------------------------------------------------------------------------


@router.patch(
    "/{project_id}",
    response_model=ProjectPublic,
    summary="Update mutable project fields (role >= team_admin)",
)
async def update_project_endpoint(
    request: Request,
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("team_admin")),
) -> Response:
    try:
        project = await update_project(
            session,
            project_id=project_id,
            payload=payload,
            actor=actor,
        )
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ProjectPublic.model_validate(project)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/projects/{project_id}  (soft-delete / archive)
# ---------------------------------------------------------------------------


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive (soft-delete) the project (role >= team_admin)",
)
async def delete_project_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("team_admin")),
) -> Response:
    try:
        await archive_project(session, project_id=project_id, actor=actor)
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# POST /v1/projects/{project_id}/scans  (trigger scan — PR #7 skeleton)
# ---------------------------------------------------------------------------


@router.post(
    "/{project_id}/scans",
    response_model=ScanPublic,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a scan for the project (PR #7 skeleton — Celery enqueue lands in PR #8)",
)
async def trigger_scan_endpoint(
    request: Request,
    project_id: uuid.UUID,
    payload: ScanCreate,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        scan = await trigger_scan(
            session,
            project_id=project_id,
            payload=payload,
            actor=actor,
        )
    except ScanError as exc:
        return _problem_for_scan_error(request, exc)

    body = ScanPublic.model_validate(scan)
    return Response(
        # `by_alias=True` so the response carries `metadata` (the API field
        # name) rather than `scan_metadata` (the ORM attribute name).
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )
