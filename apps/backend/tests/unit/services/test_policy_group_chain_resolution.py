# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group-hierarchy generalisation of policy resolution (Phase 3).

``license_policy_service.get_effective_policy`` and
``gate_policy_service.resolve_for_project`` used to assume a fixed two-tier
scope: one team, one org-default. Groups now nest without limit (migrations
0090/0091), so both resolvers walk the full ancestor chain between a group
(or a project's owning group) and the organisation. This file exercises
chains of depth >= 3 for the three semantics in play:

  - REPLACE (license policy): the nearest ancestor with an ENABLED row wins
    WHOLESALE; a disabled or absent row at one level falls through to the
    next ancestor, then the org default.
  - FALL-THROUGH (gate policy's three scalar fields): same nearest-wins idea,
    field by field, with NULL (not "disabled") as the skip signal.
  - UNION (gate policy's ``approval_required_statuses``): every ancestor's
    list, plus the organization's, ORed together. The property worth pinning
    here is not "the result is a superset of each contributor's set": that
    sentence is true of a union BY DEFINITION and would pass even if the
    implementation were quietly changed to a nearest-wins pick, so it proves
    nothing. The property that actually distinguishes union from
    nearest-wins is: an EMPTY list at a closer ancestor must NOT erase a
    FARTHER ancestor's non-empty list.

Round-trip counts pin the two-queries-regardless-of-depth contract both
resolvers' own docstrings promise. The gate side in particular is read on
every CI poll, so a per-ancestor query would make one poll's cost scale with
how deep a deployment happens to nest its groups.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import CurrentUser
from schemas.gate_policy import GatePolicyUpsertIn
from schemas.license_policy import LicensePolicyUpsertIn
from services.gate_policy_service import resolve_for_project
from services.gate_policy_service import upsert_org_policy as gate_upsert_org_policy
from services.gate_policy_service import upsert_team_policy as gate_upsert_team_policy
from services.license_policy_service import get_effective_policy
from services.license_policy_service import upsert_org_policy as license_upsert_org_policy
from services.license_policy_service import upsert_team_policy as license_upsert_team_policy
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
)

pytestmark = pytest.mark.integration

T = TypeVar("T")


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _count_statements(
    session: AsyncSession, call: Callable[[], Awaitable[T]]
) -> tuple[T, int]:
    """Run *call* and return its result plus the number of SQL statements it issued.

    Mirrors ``test_action_queue_query_count.py``'s helper of the same name,
    this codebase's established pattern for pinning a query-count contract
    rather than re-deriving the ``before_cursor_execute`` listener dance.
    """
    engine = session.get_bind()
    counted = 0

    def _record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        nonlocal counted
        counted += 1

    event.listen(engine, "before_cursor_execute", _record)
    try:
        result = await call()
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return result, counted


async def _chain(session: AsyncSession, depth: int):
    """Build a straight-line group chain of *depth* groups: root, then a child
    of the previous group *depth - 1* more times. Returns ``(org, groups)``
    with ``groups[0]`` the root and ``groups[-1]`` the leaf.
    """
    org = await make_organization(session)
    groups = []
    parent = None
    for i in range(depth):
        group = await make_team(session, organization=org, name=f"level-{i}", parent=parent)
        groups.append(group)
        parent = group
    return org, groups


async def _super_actor(session: AsyncSession):
    admin = await make_user(session, is_superuser=True)
    return principal_for(admin, role="super_admin")


async def _admin_actor_for(session: AsyncSession, group) -> CurrentUser:
    admin = await make_user(session)
    await make_membership(session, user=admin, team=group, role="group_admin")
    return principal_for(admin, team_ids=[group.id], role="group_admin")


def _license_payload(**overrides) -> LicensePolicyUpsertIn:
    base: dict = dict(
        name="chain-test",
        category_overrides={},
        license_exceptions=[],
        unknown_license_category="conditional",
        enabled=True,
    )
    base.update(overrides)
    return LicensePolicyUpsertIn(**base)


# ---------------------------------------------------------------------------
# REPLACE (license policy), depth >= 3
# ---------------------------------------------------------------------------


async def test_license_policy_skips_absent_ancestors_to_reach_a_deeper_one(
    db_session: AsyncSession,
) -> None:
    """root -> mid -> leaf (depth 3). Only ``mid`` (the leaf's parent) has a
    policy; the leaf itself and the org have none. The leaf must resolve to
    ``mid``'s row, one ancestor up, not the org default.
    """
    org, (root, mid, leaf) = await _chain(db_session, 3)
    mid_actor = await _admin_actor_for(db_session, mid)
    await license_upsert_team_policy(
        db_session, mid_actor, team_id=mid.id, payload=_license_payload(name="mid-policy")
    )

    effective = await get_effective_policy(db_session, team_id=leaf.id)

    assert effective is not None
    assert effective.team_id == mid.id
    assert effective.name == "mid-policy"


async def test_license_policy_walks_past_a_disabled_ancestor_to_the_next_one(
    db_session: AsyncSession,
) -> None:
    """root -> mid -> leaf (depth 3), all three + org carry a row. ``leaf``'s
    own row is disabled, so resolution must fall through to ``mid``, NOT
    straight to ``root`` or the org default, and not "nothing" either
    (disabled means "skip this one level", not "stop walking the chain").
    """
    org, (root, mid, leaf) = await _chain(db_session, 3)
    super_actor = await _super_actor(db_session)
    root_actor = await _admin_actor_for(db_session, root)
    mid_actor = await _admin_actor_for(db_session, mid)
    leaf_actor = await _admin_actor_for(db_session, leaf)

    await license_upsert_org_policy(
        db_session, super_actor, organization_id=org.id, payload=_license_payload(name="org")
    )
    await license_upsert_team_policy(
        db_session, root_actor, team_id=root.id, payload=_license_payload(name="root")
    )
    await license_upsert_team_policy(
        db_session, mid_actor, team_id=mid.id, payload=_license_payload(name="mid")
    )
    await license_upsert_team_policy(
        db_session,
        leaf_actor,
        team_id=leaf.id,
        payload=_license_payload(name="leaf", enabled=False),
    )

    effective = await get_effective_policy(db_session, team_id=leaf.id)

    assert effective is not None
    assert effective.team_id == mid.id
    assert effective.name == "mid"


async def test_license_policy_falls_all_the_way_to_org_default_at_depth_four(
    db_session: AsyncSession,
) -> None:
    """Depth 4, nobody in the chain has a row except the organization; the
    leaf must still reach the org default rather than stopping at None.
    """
    org, (_root, _lvl1, _lvl2, leaf) = await _chain(db_session, 4)
    super_actor = await _super_actor(db_session)
    await license_upsert_org_policy(
        db_session, super_actor, organization_id=org.id, payload=_license_payload(name="org")
    )

    effective = await get_effective_policy(db_session, team_id=leaf.id)

    assert effective is not None
    assert effective.team_id is None
    assert effective.name == "org"


async def test_license_policy_round_trip_stays_two_regardless_of_chain_depth(
    db_session: AsyncSession,
) -> None:
    """The resolver's own contract: one query for the group's ancestor
    ``path``, one for every candidate row. A chain of 5 groups must cost the
    same two round trips as a chain of 1.
    """
    org, groups = await _chain(db_session, 5)
    leaf = groups[-1]
    super_actor = await _super_actor(db_session)
    await license_upsert_org_policy(
        db_session, super_actor, organization_id=org.id, payload=_license_payload(name="org")
    )

    _, statement_count = await _count_statements(
        db_session, lambda: get_effective_policy(db_session, team_id=leaf.id)
    )

    assert statement_count == 2


# ---------------------------------------------------------------------------
# FALL-THROUGH (gate policy scalar fields), depth >= 3
# ---------------------------------------------------------------------------


def _gate_payload(**overrides) -> GatePolicyUpsertIn:
    return GatePolicyUpsertIn(**overrides)


async def test_gate_policy_scalar_field_falls_through_an_absent_ancestor(
    db_session: AsyncSession,
) -> None:
    """root -> mid -> leaf (depth 3), project on the leaf. ``root`` sets
    ``epss_threshold``; ``mid`` and the leaf have no row at all. The project
    must resolve to ``root``'s value, skipping the two empty levels.
    """
    org, (root, _mid, leaf) = await _chain(db_session, 3)
    project = await make_project(db_session, team=leaf)
    root_actor = await _admin_actor_for(db_session, root)
    await gate_upsert_team_policy(
        db_session, root_actor, team_id=root.id, payload=_gate_payload(epss_threshold=0.3)
    )

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.epss_threshold == 0.3
    assert resolved.sources["epss_threshold"].group_ids == (root.id,)


async def test_gate_policy_scalar_field_prefers_nearest_over_farther_ancestor(
    db_session: AsyncSession,
) -> None:
    """Both ``mid`` and ``root`` set ``malicious_blocks`` to conflicting
    values; the project (on ``leaf``, child of ``mid``, grandchild of
    ``root``) must see ``mid``'s value: nearest wins, not "first written" or
    "root wins".
    """
    org, (root, mid, leaf) = await _chain(db_session, 3)
    project = await make_project(db_session, team=leaf)
    root_actor = await _admin_actor_for(db_session, root)
    mid_actor = await _admin_actor_for(db_session, mid)
    await gate_upsert_team_policy(
        db_session, root_actor, team_id=root.id, payload=_gate_payload(malicious_blocks=True)
    )
    await gate_upsert_team_policy(
        db_session, mid_actor, team_id=mid.id, payload=_gate_payload(malicious_blocks=False)
    )

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.malicious_blocks is False
    assert resolved.sources["malicious_blocks"].group_ids == (mid.id,)


async def test_gate_policy_scalar_field_reaches_org_default_at_depth_four(
    db_session: AsyncSession,
) -> None:
    org, groups = await _chain(db_session, 4)
    leaf = groups[-1]
    project = await make_project(db_session, team=leaf)
    super_actor = await _super_actor(db_session)
    await gate_upsert_org_policy(
        db_session, super_actor, organization_id=org.id, payload=_gate_payload(epss_threshold=0.7)
    )

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.epss_threshold == 0.7
    assert resolved.sources["epss_threshold"].group_ids == ()
    assert resolved.sources["epss_threshold"].organization_contributed is False


async def test_gate_policy_round_trip_stays_two_regardless_of_chain_depth(
    db_session: AsyncSession,
) -> None:
    org, groups = await _chain(db_session, 6)
    leaf = groups[-1]
    project = await make_project(db_session, team=leaf)
    super_actor = await _super_actor(db_session)
    await gate_upsert_org_policy(
        db_session, super_actor, organization_id=org.id, payload=_gate_payload(epss_threshold=0.2)
    )

    _, statement_count = await _count_statements(
        db_session, lambda: resolve_for_project(db_session, project.id)
    )

    assert statement_count == 2


# ---------------------------------------------------------------------------
# UNION (gate policy's approval_required_statuses), depth >= 3
# ---------------------------------------------------------------------------


async def test_approval_required_statuses_unions_every_ancestor_and_the_org(
    db_session: AsyncSession,
) -> None:
    org, (root, mid, leaf) = await _chain(db_session, 3)
    project = await make_project(db_session, team=leaf)
    super_actor = await _super_actor(db_session)
    root_actor = await _admin_actor_for(db_session, root)
    mid_actor = await _admin_actor_for(db_session, mid)
    leaf_actor = await _admin_actor_for(db_session, leaf)

    await gate_upsert_org_policy(
        db_session,
        super_actor,
        organization_id=org.id,
        payload=_gate_payload(approval_required_statuses=["fixed"]),
    )
    await gate_upsert_team_policy(
        db_session,
        root_actor,
        team_id=root.id,
        payload=_gate_payload(approval_required_statuses=["not_affected"]),
    )
    await gate_upsert_team_policy(
        db_session,
        mid_actor,
        team_id=mid.id,
        payload=_gate_payload(approval_required_statuses=["suppressed"]),
    )
    await gate_upsert_team_policy(
        db_session,
        leaf_actor,
        team_id=leaf.id,
        payload=_gate_payload(approval_required_statuses=["false_positive"]),
    )

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.approval_required_statuses == [
        "false_positive",
        "fixed",
        "not_affected",
        "suppressed",
    ]
    source = resolved.sources["approval_required_statuses"]
    # Nearest-to-the-project first: leaf, then mid, then root.
    assert source.group_ids == (leaf.id, mid.id, root.id)
    assert source.organization_contributed is True


async def test_a_childs_empty_list_does_not_erase_an_ancestors_names(
    db_session: AsyncSession,
) -> None:
    """The property that actually distinguishes UNION from a nearest-wins pick.

    ``root`` requires approval for "suppressed". The leaf writes a row with
    ``approval_required_statuses`` set to an EXPLICIT empty list, distinct
    from omitting the field (which stores NULL, "no opinion" for THIS row,
    identical in effect but not in intent). A plain ``pick()`` (the
    fall-through the three scalar fields use) treats "not None" as "this row
    decided", so an explicit ``[]`` would win outright and erase every
    ancestor's names, exactly the CWE-863-shaped gap this field's docstring
    warns about, and the reason this field is a union instead of a
    fall-through. The result must still be ``["suppressed"]``.

    Mutation check performed for this PR: temporarily replacing the union
    call in ``resolve_for_project`` with ``pick("approval_required_statuses")``
    turns this test red (``resolved.approval_required_statuses == []``,
    ``root`` never consulted) while leaving every fall-through-field test in
    this module green, confirming this assertion actually exercises the
    union vs. nearest-wins distinction rather than passing regardless.
    """
    org, (root, _mid, leaf) = await _chain(db_session, 3)
    project = await make_project(db_session, team=leaf)
    root_actor = await _admin_actor_for(db_session, root)
    leaf_actor = await _admin_actor_for(db_session, leaf)

    await gate_upsert_team_policy(
        db_session,
        root_actor,
        team_id=root.id,
        payload=_gate_payload(approval_required_statuses=["suppressed"]),
    )
    # The leaf explicitly sends [] (not omitted), so the row stores an empty
    # list, not NULL.
    await gate_upsert_team_policy(
        db_session,
        leaf_actor,
        team_id=leaf.id,
        payload=_gate_payload(epss_threshold=0.1, approval_required_statuses=[]),
    )

    resolved = await resolve_for_project(db_session, project.id)

    assert resolved.approval_required_statuses == ["suppressed"]
    assert resolved.sources["approval_required_statuses"].group_ids == (root.id,)


async def test_approval_required_statuses_round_trip_stays_two_at_depth_five(
    db_session: AsyncSession,
) -> None:
    org, groups = await _chain(db_session, 5)
    leaf = groups[-1]
    project = await make_project(db_session, team=leaf)
    super_actor = await _super_actor(db_session)
    await gate_upsert_org_policy(
        db_session,
        super_actor,
        organization_id=org.id,
        payload=_gate_payload(approval_required_statuses=["fixed"]),
    )
    root_actor = await _admin_actor_for(db_session, groups[0])
    await gate_upsert_team_policy(
        db_session,
        root_actor,
        team_id=groups[0].id,
        payload=_gate_payload(approval_required_statuses=["suppressed"]),
    )

    _, statement_count = await _count_statements(
        db_session, lambda: resolve_for_project(db_session, project.id)
    )

    assert statement_count == 2
