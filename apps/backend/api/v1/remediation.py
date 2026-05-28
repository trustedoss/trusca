"""
Remediation HTTP surface — v2.2 2.2-b2 (dry-run) + 2.2-b3 (opt-in auto-PR).

b2 (side-effect-free):
  POST /v1/projects/{project_id}/remediation/npm/dry-run
      → compute the edited package.json + diff for the project's vulnerable npm
        dependencies. Accepts an optional uploaded manifest in the body; when
        omitted the manifest is best-effort fetched from the latest preserved
        scan source. NEVER opens a PR and NEVER persists.

b3 (privileged external write):
  POST /v1/projects/{project_id}/remediation/npm/pull-request
      → actually OPEN a pull request on the project's OPTED-IN GitHub repo that
        applies the bump. team_admin only. The target repo is NEVER caller-
        supplied — it is derived from the project's opted-in GitHub App
        installation (``services.remediation_pr_service``). Idempotent.
  GET  /v1/projects/{project_id}/remediation/pull-requests
      → list the project's remediation-PR records (any team member).

Auth (rule #12): every route requires at least ``require_role("developer")``;
the PR endpoint additionally requires team_admin, enforced in the service.
Team scoping + 404 existence-hide live in the service layer; a project the caller
cannot see returns the same 404 as a missing one, so cross-team enumeration is
closed (mirrors ``api/v1/source_tree.py``).

All 4xx / 5xx responses are RFC 7807 ``application/problem+json`` via
``core.errors.problem_response``; typed domain exceptions carry the status / title
/ type-URI so the translation is one mapping for every error.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from models import RemediationPullRequest
from schemas.remediation import (
    DependencyChangeOut,
    DryRunRecommendationOut,
    NpmDryRunRequest,
    NpmDryRunResponse,
    RemediationWarningOut,
)
from schemas.remediation_pr import (
    NpmPullRequestCreate,
    RemediationPackageChangeOut,
    RemediationPullRequestList,
    RemediationPullRequestOut,
)
from services.remediation_pr_service import (
    RemediationPRResult,
    create_npm_remediation_pr,
    list_remediation_prs,
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


# ---------------------------------------------------------------------------
# b3 — opt-in automated remediation PR
# ---------------------------------------------------------------------------


def _record_to_out(record: RemediationPullRequest) -> RemediationPullRequestOut:
    """Project a persisted record to the response schema.

    ``package_changes`` is a JSONB array of ``{"package","from","to"}`` (the
    natural wire keys); we map it through the aliased schema so ``from``/``to``
    round-trip onto the ``from_version``/``to_version`` attributes.
    """
    changes = [
        RemediationPackageChangeOut(
            package=str(item.get("package", "")),
            **{
                "from": item.get("from"),  # type: ignore[arg-type]
                "to": str(item.get("to", "")),
            },
        )
        for item in (record.package_changes or [])
        if isinstance(item, dict)
    ]
    return RemediationPullRequestOut(
        id=record.id,
        project_id=record.project_id,
        ecosystem=record.ecosystem,
        repository_full_name=record.repository_full_name,
        head_branch=record.head_branch,
        base_branch=record.base_branch,
        pr_number=record.pr_number,
        pr_url=record.pr_url,
        status=record.status,  # type: ignore[arg-type]  # CHECK-constrained literal
        package_changes=changes,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@router.post(
    "/projects/{project_id}/remediation/npm/pull-request",
    summary="Open an automated npm remediation PR on the project's opted-in repo",
    response_model=RemediationPullRequestOut,
    responses={
        200: {"description": "An existing open PR was returned (idempotent hit)."},
        201: {"description": "A new remediation PR was opened."},
        204: {"description": "Nothing to remediate (no manifest change)."},
        401: {"description": "Authentication required"},
        403: {"description": "Caller is not a team_admin of the project's team"},
        404: {"description": "Project not found / not accessible"},
        409: {"description": "Project is not opted in to automated remediation PRs"},
        422: {"description": "Manifest / stored config unusable"},
        502: {"description": "A GitHub write failed"},
    },
)
async def post_npm_pull_request(
    request: Request,
    project_id: uuid.UUID,
    body: NpmPullRequestCreate | None = Body(default=None),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> JSONResponse:
    """Open (or return the existing) automated npm remediation PR.

    team_admin RBAC + opt-in enforcement live in the service. Returns 201 for a
    freshly opened PR, 200 for an idempotent hit on an existing open PR, and 204
    when there is nothing to remediate.
    """
    manifest_override = body.manifest if body is not None else None
    try:
        result: RemediationPRResult = await create_npm_remediation_pr(
            session,
            actor,
            project_id,
            manifest_override=manifest_override,
        )
    except RemediationError as exc:
        return _problem(request, exc)

    if result.record is None:
        # Nothing to remediate — 204 No Content (no body, per HTTP semantics).
        return JSONResponse(content=None, status_code=status.HTTP_204_NO_CONTENT)

    out = _record_to_out(result.record)
    code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    # by_alias=True so package_changes uses the `from`/`to` wire keys — matching
    # the GET list endpoint (FastAPI response_model serialization is by-alias).
    return JSONResponse(content=out.model_dump(mode="json", by_alias=True), status_code=code)


@router.get(
    "/projects/{project_id}/remediation/pull-requests",
    summary="List the project's automated remediation PR records",
    response_model=RemediationPullRequestList,
    responses={
        200: {"description": "The project's remediation-PR records (newest first)."},
        401: {"description": "Authentication required"},
        404: {"description": "Project not found / not accessible"},
    },
)
async def get_remediation_pull_requests(
    request: Request,
    project_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> JSONResponse | RemediationPullRequestList:
    try:
        rows, total = await list_remediation_prs(
            session, actor, project_id, page=page, page_size=page_size
        )
    except RemediationError as exc:
        return _problem(request, exc)
    return RemediationPullRequestList(
        items=[_record_to_out(r) for r in rows], total=total
    )
