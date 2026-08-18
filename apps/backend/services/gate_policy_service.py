# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Resolving a build-gate policy for a project.

Four sources, most specific first: the project's team, the organization the
team belongs to, the environment variable, and the built-in default. Each
field falls through independently, so a team that pins one threshold does not
inherit the rest from itself; it inherits them from the organization exactly
as if it had written no row at all.

The reason every field is nullable rather than defaulted lives here. A policy
row with defaults filled in would mean "this team has decided everything",
which is almost never true: a team writes a row to change one thing. Storing
NULL for the rest keeps the organization's later change flowing through to
them, which is what an organization-wide policy is for.

Nothing in this module fails a lookup. A deployment with no rows, an
unreachable organization, a malformed value that the database somehow admitted
all resolve to the environment answer, because a gate that cannot read its
policy must fall back to the behaviour it had before the policy existed rather
than open or close on its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import _ROLE_PRIORITY, CurrentUser
from models import GatePolicy, Organization, Project, Team
from schemas.gate_policy import GatePolicyUpsertIn

log = structlog.get_logger("services.gate_policy")


class GatePolicyError(Exception):
    """Base for the failures the router turns into Problem Details."""


class GatePolicyForbidden(GatePolicyError):
    """Caller may not write at this scope."""


class GatePolicyScopeNotFound(GatePolicyError):
    """The team or organization does not exist, or is hidden from the caller."""


def _is_super_admin(actor: CurrentUser) -> bool:
    return actor.is_superuser or actor.role == "super_admin"


def _may_administer_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """Whether ``actor`` may write the policy of ``team_id``.

    Rank rather than equality. Asking "is this grade team_admin" would answer
    no for any grade added above it later, and the same phrasing elsewhere in
    the codebase had to be corrected once a grade was added below.
    """
    if _is_super_admin(actor):
        return True
    grade = actor.team_roles.get(team_id)
    if grade is None:
        return False
    return _ROLE_PRIORITY.get(grade, 0) >= _ROLE_PRIORITY["team_admin"]


def _may_read_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    """Any member may read the policy that applies to their own team."""
    return _is_super_admin(actor) or team_id in actor.team_ids


@dataclass(frozen=True)
class ResolvedGatePolicy:
    """What the gate should apply, after the fall-through.

    ``None`` on a field means "no policy decided this", and the caller uses its
    environment answer. Keeping the distinction here rather than resolving it
    to a value lets the caller's existing defaults stay the single place those
    defaults are written.
    """

    epss_threshold: float | None = None
    reachable_critical_only: bool | None = None
    malicious_blocks: bool | None = None
    #: Field name to the scope that supplied it, "team" or "organization".
    #: Fields no policy decided are absent, and the caller reports those as
    #: coming from the deployment. Without this an operator reading an
    #: effective value cannot tell which row to edit to change it.
    sources: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return (
            self.epss_threshold is None
            and self.reachable_critical_only is None
            and self.malicious_blocks is None
        )


_EMPTY = ResolvedGatePolicy()


async def resolve_for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> ResolvedGatePolicy:
    """Return the policy that applies to ``project_id``.

    One query for the team and organization, one for the two candidate rows.
    The gate runs on every CI poll, so this stays at two round trips rather
    than walking the chain a row at a time.
    """
    scope = (
        await session.execute(
            select(Team.id, Team.organization_id)
            .join(Project, Project.team_id == Team.id)
            .where(Project.id == project_id)
        )
    ).one_or_none()
    if scope is None:
        # The project vanished between the caller's check and here, or has no
        # team. Either way there is no policy to apply and the caller's
        # environment answer stands.
        return _EMPTY

    team_id, organization_id = scope
    rows = (
        await session.execute(
            select(GatePolicy).where(
                GatePolicy.organization_id == organization_id,
                # Not ``in_([team_id, None])``: SQL never matches a NULL that
                # way, so the organization default would be invisible and the
                # fall-through would silently stop at the team row.
                or_(GatePolicy.team_id == team_id, GatePolicy.team_id.is_(None)),
            )
        )
    ).scalars().all()
    if not rows:
        return _EMPTY

    team_row = next((row for row in rows if row.team_id == team_id), None)
    org_row = next((row for row in rows if row.team_id is None), None)

    sources: dict[str, str] = {}

    def pick(name: str) -> object:
        for row, scope in ((team_row, "team"), (org_row, "organization")):
            if row is None:
                continue
            value = getattr(row, name)
            if value is not None:
                sources[name] = scope
                return value
        return None

    return ResolvedGatePolicy(
        epss_threshold=pick("epss_threshold"),  # type: ignore[arg-type]
        reachable_critical_only=pick("reachable_critical_only"),  # type: ignore[arg-type]
        malicious_blocks=pick("malicious_blocks"),  # type: ignore[arg-type]
        sources=sources,
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def _resolve_team_org(session: AsyncSession, team_id: uuid.UUID) -> uuid.UUID:
    row = (
        await session.execute(select(Team.organization_id).where(Team.id == team_id))
    ).scalar_one_or_none()
    if row is None:
        raise GatePolicyScopeNotFound(f"team {team_id} not found")
    return row


async def upsert_team_policy(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    team_id: uuid.UUID,
    payload: GatePolicyUpsertIn,
) -> GatePolicy:
    """Create or replace the gate policy for one team.

    Idempotent on the scope: a second PUT updates the row rather than adding
    one, which the unique constraint would refuse anyway. A field the payload
    omits is stored as NULL, so omitting is how a team stops overriding and
    goes back to following its organization.
    """
    organization_id = await _resolve_team_org(session, team_id)
    if not _may_administer_team(actor, team_id):
        # Existence is not hidden here: the caller already knows the team, and
        # writing a policy for a team you can see but not administer is an
        # authorization answer rather than a discovery one.
        raise GatePolicyForbidden(f"actor may not write the gate policy for team {team_id}")

    return await _upsert(session, organization_id=organization_id, team_id=team_id, payload=payload)


async def upsert_org_policy(
    session: AsyncSession,
    actor: CurrentUser,
    *,
    organization_id: uuid.UUID,
    payload: GatePolicyUpsertIn,
) -> GatePolicy:
    """Create or replace the organization default.

    Super-admin only. An organization default is the floor every team inherits,
    so the grade that may set it is the one that answers for the deployment.
    """
    exists = (
        await session.execute(
            select(Organization.id).where(Organization.id == organization_id)
        )
    ).scalar_one_or_none()
    if exists is None:
        raise GatePolicyScopeNotFound(f"organization {organization_id} not found")
    if not _is_super_admin(actor):
        raise GatePolicyForbidden("only a super admin may write the organization gate policy")

    return await _upsert(session, organization_id=organization_id, team_id=None, payload=payload)


async def _upsert(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    team_id: uuid.UUID | None,
    payload: GatePolicyUpsertIn,
) -> GatePolicy:
    existing = (
        await session.execute(
            select(GatePolicy).where(
                GatePolicy.organization_id == organization_id,
                GatePolicy.team_id == team_id
                if team_id is not None
                else GatePolicy.team_id.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = GatePolicy(organization_id=organization_id, team_id=team_id)
        session.add(existing)

    existing.name = payload.name
    existing.epss_threshold = payload.epss_threshold
    existing.reachable_critical_only = payload.reachable_critical_only
    existing.malicious_blocks = payload.malicious_blocks
    await session.commit()
    await session.refresh(existing)
    return existing


async def delete_team_policy(
    session: AsyncSession, actor: CurrentUser, *, team_id: uuid.UUID
) -> bool:
    """Drop a team's row so it follows its organization again.

    Returns whether a row was removed, so the router can answer 404 for a team
    that never had one rather than reporting a delete that deleted nothing.
    """
    organization_id = await _resolve_team_org(session, team_id)
    if not _may_administer_team(actor, team_id):
        raise GatePolicyForbidden(f"actor may not write the gate policy for team {team_id}")

    row = (
        await session.execute(
            select(GatePolicy).where(
                GatePolicy.organization_id == organization_id,
                GatePolicy.team_id == team_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def get_team_policy(
    session: AsyncSession, actor: CurrentUser, *, team_id: uuid.UUID
) -> GatePolicy | None:
    """The team's own row, or None when it has not written one."""
    organization_id = await _resolve_team_org(session, team_id)
    if not _may_read_team(actor, team_id):
        # Hidden rather than refused: a caller outside the team has no business
        # learning which teams exist from this endpoint.
        raise GatePolicyScopeNotFound(f"team {team_id} not found")

    return (
        await session.execute(
            select(GatePolicy).where(
                GatePolicy.organization_id == organization_id,
                GatePolicy.team_id == team_id,
            )
        )
    ).scalar_one_or_none()
