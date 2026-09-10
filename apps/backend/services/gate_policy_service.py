# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Resolving a build-gate policy for a project.

Sources, most specific first: every group between the project and the root of
its group tree (nearest first), then the organization default, then the
environment variable, then the built-in default. Each field falls through
independently, so a group that pins one threshold does not inherit the rest
from itself; it inherits them from its parent group (or, with no ancestor
row, the organization) exactly as if it had written no row at all.

Group-hierarchy generalisation (Phase 3): groups nest without limit
(migrations 0090/0091). What used to be a fixed two-tier fall-through (team,
then organization) is now a walk of the group's own ancestor chain, with the
organization default as the chain's fixed last stop rather than a
special-cased second tier. A deployment that never nests a group below the
root sees byte-for-byte the old two-tier behaviour, because a root group's
chain has exactly one entry.

The reason every field is nullable rather than defaulted lives here. A policy
row with defaults filled in would mean "this group has decided everything",
which is almost never true: a group writes a row to change one thing. Storing
NULL for the rest keeps an ancestor's later change flowing through to it,
which is what inheritance is for.

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

from core.authz import can_access_group
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
    return _ROLE_PRIORITY.get(grade, 0) >= _ROLE_PRIORITY["group_admin"]


@dataclass(frozen=True)
class GateFieldSource:
    """Where one resolved field's value came from, service-layer / name-free.

    ``group_ids`` lists the CONTRIBUTING groups, nearest-to-the-project first.
    A fall-through field (``epss_threshold``, ``reachable_critical_only``,
    ``malicious_blocks``) has at most one entry: the single nearest ancestor
    whose row set it, exactly the group whose row an operator would edit to
    change it. ``approval_required_statuses`` is a union, so it may have more
    than one when several ancestors at different depths each added at least
    one status name.

    ``organization_contributed`` is meaningful only for the union field: True
    when the org-default row ALSO added a status name beyond whatever
    ``group_ids`` contributed. It is always False for a fall-through field,
    because a fall-through field with any group contributor never consults
    the organization at all (the nearest non-null wins outright); an
    organization-only fall-through value is represented by this dataclass
    being ABSENT from ``ResolvedGatePolicy.sources`` (see that field's own
    docstring: "group" vs "organization" vs "deployment" is a presence/shape
    test, not a stored enum, at this layer).
    """

    group_ids: tuple[uuid.UUID, ...] = ()
    organization_contributed: bool = False


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
    #: Statuses one person may not reach alone. Empty and None both mean the
    #: same thing to the caller, but the field stays optional so the fall
    #: through works the way the others do.
    approval_required_statuses: list[str] | None = None
    #: Field name -> where it came from. A field no policy decided (fell all
    #: the way through to the environment answer) is ABSENT from this dict,
    #: callers report that case as "deployment" themselves (see
    #: ``api.v1.gate_policies.effective_policy_endpoint``), rather than this
    #: dataclass inventing a group-less, org-less sentinel value for it.
    sources: Mapping[str, GateFieldSource] = field(default_factory=dict)
    #: The project's own group's ancestor chain, nearest first, AS RESOLVED
    #: (i.e. exactly the ids ``pick()``/the union walked, not every group
    #: that exists, just the ones between the project and the root). Absent
    #: (empty) when the project itself could not be resolved. Exists so a
    #: caller building a UI breadcrumb for ``sources`` (which only names the
    #: groups that CONTRIBUTED a value) can still place them within the full
    #: chain without a second lookup of "what is this project's group tree".
    chain: tuple[uuid.UUID, ...] = ()

    @property
    def is_empty(self) -> bool:
        return (
            self.epss_threshold is None
            and self.reachable_critical_only is None
            and self.malicious_blocks is None
            and self.approval_required_statuses is None
        )


_EMPTY = ResolvedGatePolicy()


async def resolve_for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> ResolvedGatePolicy:
    """Return the policy that applies to ``project_id``.

    One query for the project's group chain (its own group's id + ancestor
    ``path`` + organization), one for every candidate row (every ancestor's +
    the organization default's, in one ``IN`` list). The gate runs on every CI
    poll, so this stays at two round trips regardless of how deep the group
    tree is. Walking the chain a row at a time would make one poll's cost
    scale with nesting depth, which is exactly the regression a materialised
    ``path`` column exists to avoid (see the ``Group`` model docstring).
    """
    scope = (
        await session.execute(
            select(Team.id, Team.path, Team.organization_id)
            .join(Project, Project.team_id == Team.id)
            .where(Project.id == project_id)
        )
    ).one_or_none()
    if scope is None:
        # The project vanished between the caller's check and here, or has no
        # team. Either way there is no policy to apply and the caller's
        # environment answer stands.
        return _EMPTY

    group_id, ancestor_path, organization_id = scope
    # Nearest first: the project's own group, then its parent, ... up to (not
    # including) the root. ``ancestor_path`` is root-first (the DB trigger's
    # convention), so nearest-first is the reverse. The organization default
    # is not in this list; it is the chain's fixed last stop, handled
    # separately below (its ``group_id`` is NULL, never a member of a group
    # chain).
    chain: tuple[uuid.UUID, ...] = (group_id, *reversed(list(ancestor_path)))

    rows = (
        await session.execute(
            select(GatePolicy).where(
                GatePolicy.organization_id == organization_id,
                # Not ``in_([*chain, None])``: SQL never matches a NULL that
                # way, so the organization default would be invisible and the
                # fall-through would silently stop at the nearest ancestor row.
                or_(GatePolicy.team_id.in_(chain), GatePolicy.team_id.is_(None)),
            )
        )
    ).scalars().all()
    if not rows:
        return ResolvedGatePolicy(chain=chain)

    by_group_id = {row.team_id: row for row in rows if row.team_id is not None}
    org_row = next((row for row in rows if row.team_id is None), None)

    sources: dict[str, GateFieldSource] = {}

    def pick(name: str) -> object:
        for group_id_in_chain in chain:
            row = by_group_id.get(group_id_in_chain)
            if row is None:
                continue
            value = getattr(row, name)
            if value is not None:
                sources[name] = GateFieldSource(group_ids=(group_id_in_chain,))
                return value
        if org_row is not None:
            value = getattr(org_row, name)
            if value is not None:
                sources[name] = GateFieldSource()
                return value
        return None

    return ResolvedGatePolicy(
        epss_threshold=pick("epss_threshold"),  # type: ignore[arg-type]
        reachable_critical_only=pick("reachable_critical_only"),  # type: ignore[arg-type]
        malicious_blocks=pick("malicious_blocks"),  # type: ignore[arg-type]
        approval_required_statuses=_union_approval_statuses(chain, by_group_id, org_row, sources),
        sources=sources,
        chain=chain,
    )


def _union_approval_statuses(
    chain: tuple[uuid.UUID, ...],
    by_group_id: Mapping[uuid.UUID, GatePolicy],
    org_row: GatePolicy | None,
    sources: dict[str, GateFieldSource],
) -> list[str] | None:
    """Every ancestor's list plus the organization's, never less.

    This one field does not fall through the way the thresholds do, and the
    difference is the point. The thresholds are settings a group tunes for its
    own work; this is a control, and a control a descendant can switch off
    protects nobody. Fall-through would make it switchable: a child group's
    row storing an empty list is not None, so a plain ``pick()`` would let it
    win and erase everything an ancestor named, the exact CWE-863-shaped gap
    this table exists to close, sharpened rather than softened by unlimited
    nesting, because the deeper the tree the more ancestors a single override
    would silently erase.

    A union lets any group in the chain be stricter than its ancestors and
    never looser, which is the direction that is safe to delegate, and it
    generalises the old two-tier "team+organization" union to however many
    ancestors the project's group actually has.
    """
    contributing_group_ids: list[uuid.UUID] = []
    all_named: set[str] = set()
    # Nearest first, matching ``chain`` / ``pick()``'s order: the order
    # ``GateFieldSource.group_ids`` promises its callers.
    for group_id_in_chain in chain:
        row = by_group_id.get(group_id_in_chain)
        named = set(row.approval_required_statuses or []) if row is not None else set()
        if named:
            contributing_group_ids.append(group_id_in_chain)
            all_named |= named

    org_named = set(org_row.approval_required_statuses or []) if org_row else set()
    all_named |= org_named

    if not all_named:
        return None

    sources["approval_required_statuses"] = GateFieldSource(
        group_ids=tuple(contributing_group_ids),
        organization_contributed=bool(org_named),
    )
    return sorted(all_named)


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

    Super-admin only, because the grade that sets a deployment-wide default is
    the one that answers for the deployment.

    "Default" means different things per field, and the difference matters.
    The thresholds are a starting point a team may override in either
    direction. ``approval_required_statuses`` is a floor: a team's list is
    added to this one and cannot shrink it, because a control the people it
    applies to can switch off is not a control.
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
    existing.approval_required_statuses = payload.approval_required_statuses
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
    if not await can_access_group(session, actor, team_id):
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


async def statuses_requiring_approval(
    session: AsyncSession, project_id: uuid.UUID
) -> frozenset[str]:
    """Which finding statuses this project may not reach by one person alone.

    Empty when no policy names any, which is the default: every transition
    stays a single action until an organization decides otherwise.
    """
    policy = await resolve_for_project(session, project_id)
    named = policy.approval_required_statuses
    if not named:
        return frozenset()
    return frozenset(status for status in named if isinstance(status, str))
