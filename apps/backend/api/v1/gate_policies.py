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
from collections.abc import Mapping

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import can_access_group
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from models import Team
from schemas.gate_policy import (
    EffectiveGatePolicyOut,
    EpssAvailabilityOut,
    GatePolicyGroupRef,
    GatePolicyOut,
    GatePolicySource,
    GatePolicySourceLegacy,
    GatePolicyUpsertIn,
)
from services.epss_availability import get_epss_availability
from services.gate_policy_service import (
    GateFieldSource,
    GatePolicyForbidden,
    GatePolicyScopeNotFound,
    ResolvedGatePolicy,
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

#: The four ``ResolvedGatePolicy`` fields that carry a ``sources`` entry.
_SOURCED_FIELDS = (
    "epss_threshold",
    "reachable_critical_only",
    "malicious_blocks",
    "approval_required_statuses",
)

router = APIRouter(prefix="/v1/gate-policies", tags=["gate-policies"])
log = structlog.get_logger("gate_policies.api")


async def _build_sources(
    session: AsyncSession, resolved: ResolvedGatePolicy
) -> tuple[dict[str, GatePolicySource], dict[str, GatePolicySourceLegacy]]:
    """Turn the service layer's name-free ``GateFieldSource``s into the wire shape.

    One extra query, fetching the display NAME for every group in
    ``resolved.chain`` (that ``resolve_for_project`` deliberately does not
    do itself (it stays at two round trips because ``policy_gate`` calls it
    on every CI poll; this endpoint is the human-facing policy editor, not
    that hot path, so the extra lookup lives here instead).
    """
    name_by_group_id: Mapping[uuid.UUID, str] = {}
    if resolved.chain:
        rows = (
            await session.execute(
                select(Team.id, Team.name).where(Team.id.in_(resolved.chain))
            )
        ).all()
        name_by_group_id = {row.id: row.name for row in rows}

    def group_ref(group_id: uuid.UUID) -> GatePolicyGroupRef:
        # Ancestors of *group_id* are whatever comes AFTER it in the
        # nearest-first chain; reversed to root-first for a breadcrumb.
        try:
            idx = resolved.chain.index(group_id)
        except ValueError:  # pragma: no cover - defensive; chain always
            # contains every id a GateFieldSource can name, since both come
            # from the same resolution pass.
            idx = -1
        ancestor_ids = list(reversed(resolved.chain[idx + 1 :])) if idx >= 0 else []
        return GatePolicyGroupRef(
            id=group_id,
            name=name_by_group_id.get(group_id, str(group_id)),
            path=[name_by_group_id.get(a, str(a)) for a in ancestor_ids],
        )

    sources: dict[str, GatePolicySource] = {}
    sources_legacy: dict[str, GatePolicySourceLegacy] = {}
    for name in _SOURCED_FIELDS:
        field_source: GateFieldSource | None = resolved.sources.get(name)
        if field_source is None:
            built = GatePolicySource(scope="deployment")
        elif field_source.group_ids:
            built = GatePolicySource(
                scope="group",
                group_ids=list(field_source.group_ids),
                group_paths=[group_ref(gid) for gid in field_source.group_ids],
                organization_contributed=field_source.organization_contributed,
            )
        else:
            built = GatePolicySource(scope="organization")
        sources[name] = built
        sources_legacy[name] = built.legacy
    return sources, sources_legacy


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
    "/epss-availability",
    response_model=EpssAvailabilityOut,
    summary="Whether this deployment has EPSS data behind its thresholds",
)
async def epss_availability_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> EpssAvailabilityOut:
    """Deployment-scoped, so the policy editor can qualify a threshold it shows.

    Not derived from a project or a scan: the question an administrator has
    while setting a threshold is whether this deployment collects EPSS, and
    that has one answer regardless of which project they came from. The
    per-scan version of the question rides on the gate result instead.

    No path parameter, so nothing to authorize beyond being a signed-in
    reader: the response describes the deployment's own configuration and
    carries no project, team or finding data.
    """
    availability = await get_epss_availability(session)
    return EpssAvailabilityOut(
        available=availability.usable,
        refresh_enabled=availability.refresh_enabled,
        scored_cves=availability.scored_cves,
        last_synced_at=availability.last_synced_at,
    )


@router.get(
    "/effective/{project_id}",
    response_model=EffectiveGatePolicyOut,
    summary="What this project's build gate actually applies",
)
async def effective_policy_endpoint(
    request: Request,
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("viewer")),
) -> EffectiveGatePolicyOut | Response:
    """Resolve the policy, then fill the gaps the way the gate itself does.

    A value shown here without saying where it came from invites the wrong
    edit: an operator who sees a threshold and assumes their group set it will
    look for a row that does not exist. ``sources`` names the group(s) or
    organization that supplied each value, with ``deployment`` for the ones
    no policy decided. ``sources_legacy`` is the deprecated pre-group-hierarchy
    string form of the same information (see its own docstring).

    Security review finding (Phase 3): this endpoint used to resolve and
    return a project's policy with no team/group membership check at all --
    ``require_role("viewer")`` is a coarse, route-level floor, not a
    project-scoped one. Once ``sources`` started naming the actual
    contributing groups (this Phase), that gap widened from leaking "a
    threshold is team-set" to leaking real group names and the ancestor
    chain to any authenticated stranger. Hidden rather than refused, same as
    ``get_team_policy``'s own scope check below: existence of another
    organization's project/group is not this caller's business either.
    """
    resolved = await resolve_for_project(session, project_id)
    if resolved.chain and not await can_access_group(session, actor, resolved.chain[0]):
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail=f"project {project_id} not found",
            instance=request.url.path,
        )
    sources, sources_legacy = await _build_sources(session, resolved)
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

    # ER43: a threshold shown without saying whether anything backs it invites
    # the operator to believe the axis is active. This is the DEPLOYMENT-level
    # question, which is the one this screen can answer; the per-scan version
    # rides on the gate result.
    availability = await get_epss_availability(session)

    return EffectiveGatePolicyOut(
        project_id=project_id,
        epss_threshold=epss,
        epss_data_available=availability.usable,
        epss_refresh_enabled=availability.refresh_enabled,
        epss_scored_cves=availability.scored_cves,
        epss_last_synced_at=availability.last_synced_at,
        reachable_critical_only=reachable,
        malicious_blocks=malicious,
        approval_required_statuses=resolved.approval_required_statuses or [],
        sources=sources,
        sources_legacy=sources_legacy,
    )
