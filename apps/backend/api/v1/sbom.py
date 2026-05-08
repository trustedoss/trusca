"""
SBOM export HTTP surface — Phase 3 (Step 4).

Endpoint:
  - GET /v1/projects/{project_id}/sbom?format=...

Auth: every route requires a valid access token (``require_role("developer")``).
IDOR is enforced inline — outsiders see 404 (existence-hide), exactly as for
component detail. We use 404-not-403 here because the SBOM endpoint is the
only one in the project surface that can leak structural details (component
names, versions) about a project; matching the behaviour of
``GET /v1/components/{id}`` keeps the IDOR-leak surface uniform.

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
from services.sbom_export import (
    SBOMExportError,
    SBOMUnsupportedFormat,
    export_sbom,
)

router = APIRouter(prefix="/v1", tags=["sbom"])
log = structlog.get_logger("sbom.api")


# `format` is keyed both as a Pydantic Literal (so 422 fires for invalid values
# at the OpenAPI layer) and re-validated inside the service for defense in
# depth. The Literal mirrors ``services.sbom_export.SUPPORTED_FORMATS``.
SBOMFormat = Literal["cyclonedx-json", "cyclonedx-xml", "spdx-json", "spdx-tv"]


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    """Translate project-domain errors with existence-hide on forbidden.

    The SBOM endpoint hides existence: a non-team-member sees the same 404
    they'd see for an unknown project id. Inside the project domain a
    forbidden lookup raises :class:`ProjectForbidden`; we rewrite that to a
    404 envelope here. ProjectNotFound already has status_code=404.
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


def _problem_for_sbom_error(request: Request, exc: SBOMExportError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/sbom
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_id}/sbom",
    summary="Export SBOM for the project's latest succeeded scan",
    response_class=Response,
    responses={
        200: {
            "description": "SBOM document download",
            "content": {
                "application/json": {},
                "application/xml": {},
                "text/plain": {},
            },
        },
        401: {"description": "Authentication required"},
        404: {"description": "Project not found or not accessible"},
        422: {"description": "Unknown SBOM format"},
    },
)
async def export_project_sbom_endpoint(
    request: Request,
    project_id: uuid.UUID,
    fmt: SBOMFormat = Query(
        default="cyclonedx-json",
        alias="format",
        description="SBOM output format.",
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
    # cross_team_attempt log entry is written for any unexpected gap. This
    # is belt-and-braces with `get_project`; cheap and consistent.
    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="sbom_export",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(f"actor is not a member of team {project.team_id}"),
    )

    try:
        body, content_type, filename = await export_sbom(
            session,
            project_id=project_id,
            fmt=fmt,
        )
    except SBOMUnsupportedFormat as exc:
        return _problem_for_sbom_error(request, exc)
    except SBOMExportError as exc:  # pragma: no cover - defensive
        return _problem_for_sbom_error(request, exc)

    # Encode as UTF-8 explicitly — XML / SPDX-TV may carry non-ASCII names.
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
