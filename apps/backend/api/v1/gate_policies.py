# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Build-gate policy over HTTP (prefix ``/v1/gate-policies``).

Reads sit at the lowest grade and writes at the team's administrator, which is
the same split the licence policy uses: what blocks a build is something an
auditor needs to read and an administrator decides. The route gate is a floor
only; whether this particular caller may write this particular team's policy is
decided in the service, where the team is known.

The effective endpoint answers a different question from the row endpoints. A
row says what one scope decided; the effective view says what a project will
actually be judged by, and which scope each value came from. Operators ask the
second question and the first is only useful for editing.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from schemas.gate_policy import (
    EffectiveGatePolicyOut,
    GatePolicyOut,
    GatePolicyUpsertIn,
)
from services.gate_policy_service import (
    GatePolicyForbidden,
    GatePolicyScopeNotFound,
    delete_team_policy,
    get_team_policy,
    resolve_for_project,
    upsert_org_policy,
    upsert_team_policy,
)
from services.policy_gate import (
    _resolve_epss_threshold,
    _resolve_gate_malicious_enabled,
    _resolve_reachable_critical_only,
)

router = APIRouter(prefix="/v1/gate-policies", tags=["gate-policies"])
log = structlog.get_logger("gate_policies.api")


def _problem_for(request: Request, exc: Exception) -> Response:
    if isinstance(exc, GatePolicyScopeNotFound):
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=str(exc),
            instance=request.url.path,
        )
    return problem_response(
        status_code=status.HTTP_403_FORBIDDEN,
        title="Forbidden",
        detail=str(exc),
        instance=request.url.path,
    )


@router.put(
    "/teams/{team_id}",
    response_model=GatePolicyOut,
    summary="Create or replace a team's build-gate policy",
    responses={
        200: {"description": "Policy created or replaced (idempotent on the scope)."},
        403: {"description": "Caller does not administer this team."},
        404: {"description": "Team not found."},
    },
)
async def upsert_team_policy_endpoint(
    request: Request,
    team_id: uuid.UUID,
    payload: GatePolicyUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await upsert_team_policy(session, actor, team_id=team_id, payload=payload)
    except (GatePolicyForbidden, GatePolicyScopeNotFound) as exc:
        return _problem_for(request, exc)
    return Response(
        content=GatePolicyOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.put(
    "/org/{organization_id}",
    response_model=GatePolicyOut,
    summary="Create or replace the organization default",
    responses={
        403: {"description": "Only a super admin may set the organization default."},
        404: {"description": "Organization not found."},
    },
)
async def upsert_org_policy_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: GatePolicyUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        row = await upsert_org_policy(
            session, actor, organization_id=organization_id, payload=payload
        )
    except (GatePolicyForbidden, GatePolicyScopeNotFound) as exc:
        return _problem_for(request, exc)
    return Response(
        content=GatePolicyOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.get(
    "/teams/{team_id}",
    response_model=GatePolicyOut,
    summary="Read a team's own policy row",
    responses={404: {"description": "Team not found, or it has written no policy."}},
)
async def get_team_policy_endpoint(
    request: Request,
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> Response:
    try:
        row = await get_team_policy(session, actor, team_id=team_id)
    except GatePolicyScopeNotFound as exc:
        return _problem_for(request, exc)
    if row is None:
        # A team with no row of its own is not an error, but this endpoint
        # returns rows: the caller wanting "what applies here" asks the
        # effective endpoint instead.
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"team {team_id} has no gate policy of its own",
            instance=request.url.path,
        )
    return Response(
        content=GatePolicyOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )


@router.delete(
    "/teams/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Drop a team's policy so it follows its organization again",
    responses={
        204: {"description": "Row removed."},
        403: {"description": "Caller does not administer this team."},
        404: {"description": "Team not found, or it had no policy of its own."},
    },
)
async def delete_team_policy_endpoint(
    request: Request,
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
) -> Response:
    try:
        removed = await delete_team_policy(session, actor, team_id=team_id)
    except (GatePolicyForbidden, GatePolicyScopeNotFound) as exc:
        return _problem_for(request, exc)
    if not removed:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"team {team_id} has no gate policy of its own",
            instance=request.url.path,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/effective/{project_id}",
    response_model=EffectiveGatePolicyOut,
    summary="What this project's build gate actually applies",
)
async def effective_policy_endpoint(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> EffectiveGatePolicyOut:
    """Resolve the policy, then fill the gaps the way the gate itself does.

    A value shown here without saying where it came from invites the wrong
    edit: an operator who sees a threshold and assumes their team set it will
    look for a team row that does not exist. ``sources`` names the scope that
    supplied each value, with ``deployment`` for the ones no policy decided.
    """
    resolved = await resolve_for_project(session, project_id)

    sources = {
        name: resolved.sources.get(name, "deployment")
        for name in ("epss_threshold", "reachable_critical_only", "malicious_blocks")
    }
    epss = (
        resolved.epss_threshold
        if resolved.epss_threshold is not None
        else _resolve_epss_threshold()
    )
    reachable = (
        resolved.reachable_critical_only
        if resolved.reachable_critical_only is not None
        else _resolve_reachable_critical_only()
    )
    malicious = (
        resolved.malicious_blocks
        if resolved.malicious_blocks is not None
        else _resolve_gate_malicious_enabled()
    )

    return EffectiveGatePolicyOut(
        project_id=project_id,
        epss_threshold=epss,
        reachable_critical_only=reachable,
        malicious_blocks=malicious,
        sources=sources,
    )
