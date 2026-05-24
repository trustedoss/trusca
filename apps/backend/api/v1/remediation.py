"""
Remediation dry-run HTTP surface — v2.2 2.2-b2.

One write-shaped (but side-effect-free) endpoint that previews the npm
manifest edit for a project:

  POST /v1/projects/{project_id}/remediation/npm/dry-run
      → compute the edited package.json + diff for the project's vulnerable npm
        dependencies. Accepts an optional uploaded manifest in the body; when
        omitted the manifest is best-effort fetched from the latest preserved
        scan source. NEVER opens a PR and NEVER persists (that is b3).

Auth (rule #12): every route requires ``require_role("developer")`` (role ≥
developer). Team scoping + 404 existence-hide live in the service
(``services.remediation_service``); a project the caller cannot see returns the
same 404 as a missing one, so cross-team enumeration is closed (mirrors
``api/v1/source_tree.py``).

All 4xx / 5xx responses are RFC 7807 ``application/problem+json`` via
``core.errors.problem_response``; typed domain exceptions carry the status / title
/ type-URI so the translation is one mapping for every error.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Body, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.remediation import (
    DependencyChangeOut,
    DryRunRecommendationOut,
    NpmDryRunRequest,
    NpmDryRunResponse,
    RemediationWarningOut,
)
from services.remediation_service import (
    DryRunResult,
    RemediationError,
    compute_npm_dry_run,
)

router = APIRouter(prefix="/v1", tags=["remediation"])
log = structlog.get_logger("remediation.api")


def _problem(request: Request, exc: RemediationError) -> JSONResponse:
    """Translate a typed remediation error to an RFC 7807 problem response."""
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
    )


def _to_response(result: DryRunResult) -> NpmDryRunResponse:
    return NpmDryRunResponse(
        project_id=result.project_id,
        scan_id=result.scan_id,
        ecosystem=result.ecosystem,
        manifest_source=result.manifest_source,
        manifest_found=result.manifest_found,
        changed=result.changed,
        edited_manifest=result.edited_manifest,
        recommendations=[
            DryRunRecommendationOut(
                package=r.package,
                current_version=r.current_version,
                recommended_version=r.recommended_version,
            )
            for r in result.recommendations
        ],
        changes=[
            DependencyChangeOut(
                package=c.package,
                section=c.section,
                before=c.before,
                after=c.after,
                changed=c.changed,
            )
            for c in result.changes
        ],
        warnings=[
            RemediationWarningOut(code=w.code, package=w.package, detail=w.detail)
            for w in result.warnings
        ],
        notes=list(result.notes),
    )


@router.post(
    "/projects/{project_id}/remediation/npm/dry-run",
    summary="Preview the npm dependency-bump edit for a project (dry-run, no PR)",
    response_model=NpmDryRunResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"description": "Computed dry-run (may be a no-op / no-manifest)."},
        401: {"description": "Authentication required"},
        404: {"description": "Project not found / not accessible"},
        422: {"description": "Supplied/fetched package.json could not be edited"},
    },
)
async def post_npm_dry_run(
    request: Request,
    project_id: uuid.UUID,
    body: NpmDryRunRequest | None = Body(default=None),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> JSONResponse | NpmDryRunResponse:
    manifest_override = body.manifest if body is not None else None
    try:
        result = await compute_npm_dry_run(
            session,
            actor,
            project_id,
            manifest_override=manifest_override,
        )
    except RemediationError as exc:
        return _problem(request, exc)
    return _to_response(result)
