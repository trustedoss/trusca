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
from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import can_access_group
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_role
from models import GatePolicy, Team
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
    GATE_POLICY_CONFLICT_TYPE,
    GATE_POLICY_NOT_SET_TYPE,
    GateFieldSource,
    GatePolicyConflict,
    GatePolicyForbidden,
    GatePolicyScopeNotFound,
    ResolvedGatePolicy,
    WritePrecondition,
    delete_team_policy,
    gate_policy_etag,
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
    if isinstance(exc, GatePolicyConflict):
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="Gate policy changed",
            detail=str(exc),
            instance=request.url.path,
            type_=GATE_POLICY_CONFLICT_TYPE,
        )
    return problem_response(
        status_code=status.HTTP_403_FORBIDDEN,
        title="Forbidden",
        detail=str(exc),
        instance=request.url.path,
    )


def _not_set_problem(request: Request, team_id: uuid.UUID) -> Response:
    """A visible team with no row of its own: its own type, not a bare 404.

    Only reached after the scope check passed, so it tells the caller nothing
    about a team it may not see.
    """
    return problem_response(
        status_code=status.HTTP_404_NOT_FOUND,
        title="Gate policy not set",
        detail=f"team {team_id} has no gate policy of its own",
        instance=request.url.path,
        type_=GATE_POLICY_NOT_SET_TYPE,
    )


def _entity_tags(raw: str) -> tuple[str, ...]:
    """The tags in an ``If-Match`` value, unquoted.

    ``W/`` is dropped rather than refused: a proxy that compresses the response
    may weaken the tag on the way out, and the browser then echoes the weak
    form back. A value with no recognisable tag yields an empty tuple, which
    matches nothing, so a malformed header is refused as stale rather than
    treated as absent.
    """
    tags: list[str] = []
    for token in raw.split(","):
        candidate = token.strip()
        if candidate == "*":
            tags.append("*")
            continue
        candidate = candidate.removeprefix("W/").strip('"')
        if candidate:
            tags.append(candidate)
    return tuple(tags)


def _precondition(if_match: str | None, if_none_match: str | None) -> WritePrecondition:
    return WritePrecondition(
        if_match=_entity_tags(if_match) if if_match is not None else None,
        if_none_match_any=if_none_match is not None and if_none_match.strip() == "*",
    )


def _row_response(row: GatePolicy) -> Response:
    response = Response(
        content=GatePolicyOut.model_validate(row).model_dump_json(),
        media_type="application/json",
    )
    response.headers["ETag"] = f'"{gate_policy_etag(row)}"'
    return response


_ETAG_HEADER = {
    "ETag": {
        "description": (
            "Entity tag of the row. Send it back as If-Match on the next PUT "
            "so a save made in between is not overwritten."
        ),
        "schema": {"type": "string"},
    }
}

_CONFLICT_RESPONSE = {
    "description": (
        "If-Match no longer matches the row (somebody saved in between, or the "
        "row was removed), or If-None-Match: * was sent and a row already "
        f"exists. Problem type {GATE_POLICY_CONFLICT_TYPE}. Read the policy "
        "again and reapply the edit."
    ),
    "content": {
        "application/problem+json": {
            "schema": {"$ref": "#/components/schemas/ProblemDetail"},
            "example": {
                "type": GATE_POLICY_CONFLICT_TYPE,
                "title": "Gate policy changed",
                "status": 409,
                "detail": (
                    "the gate policy changed after it was read; read it again "
                    "and reapply the edit"
                ),
                "instance": "/v1/gate-policies/teams/00000000-0000-0000-0000-000000000000",
            },
        }
    },
}

_NOT_SET_RESPONSE = {
    "description": (
        "Team not found or hidden from the caller (a plain Not Found), or the "
        "team has written no policy of its own (problem type "
        f"{GATE_POLICY_NOT_SET_TYPE}). Only the second means the team follows "
        "its organization."
    ),
    "content": {
        "application/problem+json": {
            "schema": {"$ref": "#/components/schemas/ProblemDetail"},
            "example": {
                "type": GATE_POLICY_NOT_SET_TYPE,
                "title": "Gate policy not set",
                "status": 404,
                "detail": (
                    "team 00000000-0000-0000-0000-000000000000 has no gate "
                    "policy of its own"
                ),
                "instance": "/v1/gate-policies/teams/00000000-0000-0000-0000-000000000000",
            },
        }
    },
}

#: DELETE checks the grade before the row, so a team that exists but that the
#: caller does not administer (hidden or not) answers 403, never this.
_DELETE_NOT_FOUND_RESPONSE = {
    **_NOT_SET_RESPONSE,
    "description": (
        "Team not found (a plain Not Found), or the team you administer has "
        f"no policy of its own to remove (problem type {GATE_POLICY_NOT_SET_TYPE})."
    ),
}

_IF_MATCH_DESCRIPTION = (
    "Entity tag from the last GET or PUT. When present and stale the write is "
    "refused with 409. When absent the write replaces the row unconditionally, "
    "as it did before the header was supported."
)
_IF_NONE_MATCH_DESCRIPTION = (
    "Send `*` to create the row only if none exists yet; a row that already "
    "exists answers 409."
)


@router.put(
    "/teams/{team_id}",
    response_model=GatePolicyOut,
    summary="Create or replace a team's build-gate policy",
    responses={
        200: {
            "description": "Policy created or replaced (idempotent on the scope).",
            "headers": _ETAG_HEADER,
        },
        403: {"description": "Caller does not administer this team."},
        404: {"description": "Team not found."},
        409: _CONFLICT_RESPONSE,
    },
)
async def upsert_team_policy_endpoint(
    request: Request,
    team_id: uuid.UUID,
    payload: GatePolicyUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
    if_match: str | None = Header(
        default=None, alias="if-match", description=_IF_MATCH_DESCRIPTION
    ),
    if_none_match: str | None = Header(
        default=None, alias="if-none-match", description=_IF_NONE_MATCH_DESCRIPTION
    ),
) -> Response:
    try:
        row = await upsert_team_policy(
            session,
            actor,
            team_id=team_id,
            payload=payload,
            precondition=_precondition(if_match, if_none_match),
        )
    except (GatePolicyForbidden, GatePolicyScopeNotFound, GatePolicyConflict) as exc:
        return _problem_for(request, exc)
    return _row_response(row)


@router.put(
    "/org/{organization_id}",
    response_model=GatePolicyOut,
    summary="Create or replace the organization default",
    responses={
        200: {
            "description": "Organization default created or replaced.",
            "headers": _ETAG_HEADER,
        },
        403: {"description": "Only a super admin may set the organization default."},
        404: {"description": "Organization not found."},
        409: _CONFLICT_RESPONSE,
    },
)
async def upsert_org_policy_endpoint(
    request: Request,
    organization_id: uuid.UUID,
    payload: GatePolicyUpsertIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_role("developer")),
    if_match: str | None = Header(
        default=None, alias="if-match", description=_IF_MATCH_DESCRIPTION
    ),
    if_none_match: str | None = Header(
        default=None, alias="if-none-match", description=_IF_NONE_MATCH_DESCRIPTION
    ),
) -> Response:
    try:
        row = await upsert_org_policy(
            session,
            actor,
            organization_id=organization_id,
            payload=payload,
            precondition=_precondition(if_match, if_none_match),
        )
    except (GatePolicyForbidden, GatePolicyScopeNotFound, GatePolicyConflict) as exc:
        return _problem_for(request, exc)
    return _row_response(row)


@router.get(
    "/teams/{team_id}",
    response_model=GatePolicyOut,
    summary="Read a team's own policy row",
    responses={
        200: {"description": "The team's own row.", "headers": _ETAG_HEADER},
        404: _NOT_SET_RESPONSE,
    },
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
        return _not_set_problem(request, team_id)
    return _row_response(row)


@router.delete(
    "/teams/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Drop a team's policy so it follows its organization again",
    responses={
        204: {"description": "Row removed."},
        403: {"description": "Caller does not administer this team."},
        404: _DELETE_NOT_FOUND_RESPONSE,
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
        return _not_set_problem(request, team_id)
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

    This endpoint used to resolve and return a project's policy with no
    team/group membership check at all -- ``require_role("viewer")`` is a
    coarse, route-level floor, not a project-scoped one. Once ``sources``
    started naming the actual contributing groups, that gap widened from
    leaking "a threshold is team-set" to leaking real group names and the
    ancestor chain to any authenticated stranger. Hidden rather than
    refused, same as ``get_team_policy``'s own scope check below: existence
    of another organization's project/group is not this caller's business
    either.
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
