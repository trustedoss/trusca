"""
License policy management API — v2.2 (Track C — c1).

Endpoints (prefix ``/v1/license-policies``, tag ``license-policies``):
  PUT    /teams/{team_id}        Upsert a team's license policy (team_admin).
  GET    /teams/{team_id}        Read the EFFECTIVE policy for a team (member).
  DELETE /teams/{team_id}        Reset (delete) a team's policy (team_admin).
  PUT    /org/{organization_id}  Upsert the org-default policy (super_admin).
  GET    /org/{organization_id}  Read the org-default policy (super_admin).

All 4xx / 5xx responses are RFC 7807 ``application/problem+json``.

Auth: every endpoint requires a valid JWT. The team endpoints gate on
``require_role("developer")`` (any authenticated user) and then defer the real
per-team RBAC to :mod:`services.license_policy_service` (team membership for
reads, ``team_admin`` for writes) so a cross-team write cannot slip through. The
org endpoints use ``require_super_admin_or_404`` (admin existence-hide).

This PR (c1) ships the CRUD surface only. The policy GATE that consults these
rows (and the SPDX compound/adversarial hardening) is c2 —
``services.policy_gate`` is untouched here.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role, require_super_admin_or_404
from schemas.license_policy import (
    LicenseException,
    LicensePolicyListPage,
    LicensePolicyOut,
    LicensePolicyUpsertIn,
)
from services.license_policy_service import (
    LicensePolicyError,
    add_license_exception,
    delete_team_policy,
    get_org_policy,
    get_policy,
    list_policies,
    remove_license_exception,
    upsert_org_policy,
    upsert_team_policy,
)

router = APIRouter(prefix="/v1/license-policies", tags=["license-policies"])
log = structlog.get_logger("license_policies.api")


# ---------------------------------------------------------------------------
# Error translation helper
# ---------------------------------------------------------------------------


def _problem_for_policy_error(request: Request, exc: LicensePolicyError) -> Response:
    extensions: dict[str, Any] = {}
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,
    )


def _json(body: LicensePolicyOut | LicensePolicyListPage, status_code: int) -> Response:
    return Response(
        content=body.model_dump_json(),
        status_code=status_code,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PUT /v1/license-policies/teams/{team_id}
# ---------------------------------------------------------------------------


@router.put(
    "/teams/{team_id}",
    response_model=LicensePolicyOut,
    summary="Create or update a team's license policy",
    responses={
        200: {"description": "Policy created or updated (idempotent upsert)."},
        403: {"description": "Caller is not a team_admin of this team."},
        404: {"description": "Team not found (existence-hidden)."},
        409: {"description": "Uniqueness conflict on the (org, team) scope."},
        422: {"description": "Malformed / oversized policy payload."},
    },
)
async def upsert_team_policy_endpoint(
    request: Request,
    team_id: uuid.UUID,
    payload: LicensePolicyUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await upsert_team_policy(session, actor, team_id=team_id, payload=payload)
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)
    return _json(LicensePolicyOut.model_validate(row), status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# POST/DELETE /v1/license-policies/teams/{team_id}/exceptions
#   The "waive this component" shortcut — add/remove one license_exceptions
#   entry without a read-modify-write of the whole policy.
# ---------------------------------------------------------------------------


@router.post(
    "/teams/{team_id}/exceptions",
    response_model=LicensePolicyOut,
    summary="Waive a license (add a license_exceptions entry to the team policy)",
    responses={
        200: {"description": "Exception added/updated (idempotent on spdx_id+purl)."},
        403: {"description": "Caller is not a team_admin of this team."},
        404: {"description": "Team not found (existence-hidden)."},
        422: {"description": "Malformed exception, or too many exceptions."},
    },
)
async def add_team_exception_endpoint(
    request: Request,
    team_id: uuid.UUID,
    payload: LicenseException,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await add_license_exception(
            session, actor, team_id=team_id, exception=payload
        )
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)
    return _json(LicensePolicyOut.model_validate(row), status.HTTP_200_OK)


@router.delete(
    "/teams/{team_id}/exceptions",
    response_model=LicensePolicyOut,
    summary="Un-waive (remove a license_exceptions entry from the team policy)",
    responses={
        200: {"description": "Exception removed (idempotent — no-op if absent)."},
        403: {"description": "Caller is not a team_admin of this team."},
        404: {"description": "Team or policy not found."},
    },
)
async def remove_team_exception_endpoint(
    request: Request,
    team_id: uuid.UUID,
    spdx_id: str = Query(..., min_length=1, max_length=128),
    component_purl: str | None = Query(default=None, max_length=512),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await remove_license_exception(
            session,
            actor,
            team_id=team_id,
            spdx_id=spdx_id,
            component_purl=component_purl,
        )
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)
    return _json(LicensePolicyOut.model_validate(row), status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /v1/license-policies/teams/{team_id}
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team_id}",
    response_model=LicensePolicyOut,
    summary="Read the effective license policy for a team",
    responses={
        200: {"description": "The effective policy (team override, else org default)."},
        403: {"description": "Caller is not a member of this team."},
        404: {
            "description": (
                "No enabled policy applies (the team falls back to the static catalog)."
            )
        },
    },
)
async def get_team_policy_endpoint(
    request: Request,
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await get_policy(session, actor, team_id=team_id)
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)
    return _json(LicensePolicyOut.model_validate(row), status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# DELETE /v1/license-policies/teams/{team_id}
# ---------------------------------------------------------------------------


@router.delete(
    "/teams/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reset (delete) a team's license policy",
    responses={
        204: {"description": "Policy deleted; team falls back to org default / static."},
        403: {"description": "Caller is not a team_admin of this team."},
        404: {"description": "Team or policy not found (existence-hidden)."},
    },
)
async def delete_team_policy_endpoint(
    request: Request,
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        await delete_team_policy(session, actor, team_id=team_id)
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# GET /v1/license-policies  (paginated list)
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=LicensePolicyListPage,
    summary="Paginated list of license policies visible to the caller",
)
async def list_policies_endpoint(
    request: Request,
    organization_id: uuid.UUID | None = Query(default=None),
    team_id: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        rows, total = await list_policies(
            session,
            actor,
            organization_id=organization_id,
            team_id=team_id,
            page=page,
            page_size=page_size,
        )
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)

    body = LicensePolicyListPage(
        items=[LicensePolicyOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
    return _json(body, status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# PUT /v1/license-policies/org/{organization_id}
# ---------------------------------------------------------------------------


@router.put(
    "/org/{organization_id}",
    response_model=LicensePolicyOut,
    summary="Create or update the org-default license policy (super_admin)",
    responses={
        200: {"description": "Org-default policy created or updated."},
        404: {"description": "Not found (non-super-admin existence-hide)."},
        409: {"description": "An org-default policy already exists (race)."},
        422: {"description": "Malformed / oversized policy payload."},
    },
)
async def upsert_org_policy_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: LicensePolicyUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        row = await upsert_org_policy(
            session, actor, organization_id=organization_id, payload=payload
        )
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)
    return _json(LicensePolicyOut.model_validate(row), status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# GET /v1/license-policies/org/{organization_id}
# ---------------------------------------------------------------------------


@router.get(
    "/org/{organization_id}",
    response_model=LicensePolicyOut,
    summary="Read the org-default license policy (super_admin)",
    responses={
        200: {"description": "The org-default policy."},
        404: {"description": "No org default, or non-super-admin existence-hide."},
    },
)
async def get_org_policy_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    try:
        row = await get_org_policy(session, actor, organization_id=organization_id)
    except LicensePolicyError as exc:
        return _problem_for_policy_error(request, exc)
    return _json(LicensePolicyOut.model_validate(row), status.HTTP_200_OK)


__all__ = ["router"]
