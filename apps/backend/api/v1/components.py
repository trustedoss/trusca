"""
Component detail API — Phase 3 PR #10 (task 3.3).

Single endpoint:

  GET /v1/components/{component_id}   Drawer payload for a single component.

The id is a ``component_versions.id`` because that is the identity the UI
list endpoint (`GET /v1/projects/{id}/components`) emits in each row's `id`
field. The service resolves the cv → owning team via the project's latest
scan and applies the standard team-membership IDOR guard. Components only
visible inside teams the actor cannot read return 404 (existence-hiding) —
see `services.project_detail_service.get_component_detail` for the rationale.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.project_detail import ComponentDetailResponse, ObligationRef, VulnerabilityRef
from services.project_detail_service import get_component_detail
from services.project_service import ProjectError

router = APIRouter(prefix="/v1/components", tags=["components"])
log = structlog.get_logger("components.api")


def _problem_for_project_error(request: Request, exc: ProjectError) -> Response:
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


@router.get(
    "/{component_id}",
    response_model=ComponentDetailResponse,
    summary="Component detail (drawer payload). 404 if component is invisible to caller.",
)
async def get_component_detail_endpoint(
    request: Request,
    component_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        payload = await get_component_detail(
            session,
            component_version_id=component_id,
            actor=actor,
        )
    except ProjectError as exc:
        return _problem_for_project_error(request, exc)

    body = ComponentDetailResponse(
        id=payload["id"],
        project_id=payload["project_id"],
        name=payload["name"],
        version=payload["version"],
        purl=payload["purl"],
        license=payload["license"],
        license_category=payload["license_category"],
        severity_max=payload["severity_max"],
        vulnerabilities=[VulnerabilityRef.model_validate(v) for v in payload["vulnerabilities"]],
        # M-20 — license obligations for the drawer's Obligations section.
        obligations=[ObligationRef.model_validate(o) for o in payload["obligations"]],
        raw_data=payload["raw_data"],
        depth=payload["depth"],
        direct=payload["direct"],
        # Pre-existing drop fixed alongside M-20: the service has computed
        # this since W2 #31 but the hand-built response omitted it, so the
        # wire payload was always ``null``.
        dependency_scope=payload["dependency_scope"],
        # Phase M — same hand-built-response drop class as the line above
        # (caught by the components_eol e2e: DB and list carried the verdict,
        # the drawer read null).
        eol_state=payload["eol_state"],
        eol_product=payload["eol_product"],
        eol_cycle=payload["eol_cycle"],
        eol_date=payload["eol_date"],
        eol_source=payload["eol_source"],
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
