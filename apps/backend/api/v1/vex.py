"""
VEX export HTTP surface — v2.1 Track A (A1).

Endpoint:
  - GET /v1/projects/{project_id}/vex?format=openvex|cyclonedx

Auth: every route requires a valid access token (``require_role("developer")``).
IDOR is enforced inline — outsiders see 404 (existence-hide), exactly as for the
SBOM export and component detail. We use 404-not-403 here because the VEX
endpoint leaks structural details (component purls, CVE ids, triage state) about
a project; matching the SBOM endpoint's behaviour keeps the IDOR-leak surface
uniform.

All 4xx / 5xx responses are RFC 7807 problem+json; the success response is a
file download with ``Content-Disposition: attachment``.
"""

from __future__ import annotations

import uuid
from typing import Literal

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from services.project_service import (
    ProjectError,
    ProjectForbidden,
    ProjectNotFound,
    get_project,
)
from services.vex_export import (
    VEXExportError,
    VEXUnsupportedFormat,
    export_vex,
)

router = APIRouter(prefix="/v1", tags=["vex"])
log = structlog.get_logger("vex.api")


# `format` is keyed both as a Pydantic Literal (so 422 fires for invalid values
# at the OpenAPI layer) and re-validated inside the service for defense in
# depth. The Literal mirrors ``services.vex_export.SUPPORTED_FORMATS``.
VEXFormat = Literal["openvex", "cyclonedx"]


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    """Translate project-domain errors with existence-hide on forbidden.

    The VEX endpoint hides existence: a non-team-member sees the same 404 they'd
    see for an unknown project id. Inside the project domain a forbidden lookup
    raises :class:`ProjectForbidden`; we rewrite that to a 404 envelope here.
    ProjectNotFound already has status_code=404.
    """
    if isinstance(exc, ProjectForbidden):
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Project Not Found",
            detail="Project not found.",
            instance=request.url.path,
        )
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _problem_for_vex_error(request: Request, exc: VEXExportError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/vex
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/vex",
    summary="Export a VEX document from the project's current finding triage",
    response_class=Response,
    responses={
        200: {
            "description": "VEX document download",
            "content": {
                "application/json": {},
            },
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not found or not accessible"},
        422: {"description": "Unknown VEX format"},
    },
)
async def export_project_vex_endpoint(
    request: Request,
    project_id: uuid.UUID,
    fmt: VEXFormat = Query(
        default="openvex",
        alias="format",
        description="VEX output format.",
    ),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    # IDOR guard — re-use ``get_project`` so the "is the actor allowed to see
    # this project?" decision lives in exactly one place. Existence-hide: any
    # ProjectForbidden here surfaces as 404 to outsiders (see helper above).
    try:
        project = await get_project(session, project_id=project_id, actor=actor)
    except ProjectNotFound as exc:
        return _problem_for_project_error(request, exc)
    except ProjectForbidden as exc:
        return _problem_for_project_error(request, exc)
    except ProjectError as exc:  # pragma: no cover - defensive catch-all
        return _problem_for_project_error(request, exc)

    # Re-assert team membership through the central audit helper so the
    # cross_team_attempt log entry is written for any unexpected gap. This is
    # belt-and-braces with `get_project`; cheap and consistent.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="vex_export",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(f"actor is not a member of team {project.team_id}"),
    )

    try:
        body, content_type, filename = await export_vex(
            session,
            project_id=project_id,
            fmt=fmt,
        )
    except VEXUnsupportedFormat as exc:
        return _problem_for_vex_error(request, exc)
    except VEXExportError as exc:  # pragma: no cover - defensive
        return _problem_for_vex_error(request, exc)

    # ``Content-Disposition: attachment`` makes browsers offer "save as".
    headers = {
        "content-disposition": f'attachment; filename="{filename}"',
    }
    return Response(
        content=body.encode("utf-8"),
        status_code=status.HTTP_200_OK,
        media_type=content_type,
        headers=headers,
    )
