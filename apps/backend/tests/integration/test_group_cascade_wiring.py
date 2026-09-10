# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group-hierarchy permission cascade, Phase 2 PR 2-C (wiring).

PR 2-A proved the cascade's pure functions (``services.group_service``)
against a real group tree, in isolation from every caller
(``tests/unit/services/test_group_service_cascade.py``). PR 2-B confirmed
``CurrentUser.team_roles`` already has the "direct memberships only" shape
those functions expect. This file is PR 2-C's own contribution to that
verification chain: the same tree, the same four(+one) destructive
combinations, but driven through the ACTUAL wired call sites this PR
converted, on three different surfaces:

  1. ``services.project_service.list_projects``, a *fan-out* choke-point
     (``core.authz.team_scope_filter``), one of the "6 direct clamp" sites.
  2. ``services.scan_service.list_scans_for_actor``, a second, independent
     fan-out surface (same choke-point, different model/join shape), proving
     the choke-point's cascade behaviour is not an accident of one query
     shape.
  3. ``services.project_service.archive_project``, a *single-resource* gate
     (``core.authz.can_access_group``), one of the "7 local reimplementation"
     sites this PR deleted (``_can_access_team``) and rewired.

Every test below runs at BOTH ``GROUP_CASCADE_ENABLED`` states. The off-state
assertion is the regression guard the task's own DoD calls the "most
important part" of this PR: with the flag off (still the default after this
PR merges), every one of these surfaces must produce EXACTLY today's flat,
direct-membership-only answer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    principal_for,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(params=[True, False], ids=["cascade_on", "cascade_off"])
def cascade_enabled(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> bool:
    enabled: bool = request.param
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true" if enabled else "false")
    return enabled


# ---------------------------------------------------------------------------
# Tree: P (root) -> C (child of P) -> G (grandchild of P via C)
#              \-> S (sibling of C, also a child of P)
#
# One project per group, so "which projects are visible" pins exactly onto
# "which groups are accessible", mirrors PR 2-A's tree shape/vocabulary.
# ---------------------------------------------------------------------------


class _Fixture:
    def __init__(
        self,
        *,
        p: uuid.UUID,
        c: uuid.UUID,
        g: uuid.UUID,
        s: uuid.UUID,
        project_p: uuid.UUID,
        project_c: uuid.UUID,
        project_g: uuid.UUID,
        project_s: uuid.UUID,
    ) -> None:
        self.p = p
        self.c = c
        self.g = g
        self.s = s
        self.project_p = project_p
        self.project_c = project_c
        self.project_g = project_g
        self.project_s = project_s


@pytest.fixture
async def fixture(db_session: AsyncSession) -> _Fixture:
    org = await make_organization(db_session)
    p = await make_team(db_session, organization=org)
    c = await make_team(db_session, organization=org, parent=p)
    g = await make_team(db_session, organization=org, parent=c)
    s = await make_team(db_session, organization=org, parent=p)

    project_p = await make_project(db_session, team=p)
    project_c = await make_project(db_session, team=c)
    project_g = await make_project(db_session, team=g)
    project_s = await make_project(db_session, team=s)

    return _Fixture(
        p=p.id,
        c=c.id,
        g=g.id,
        s=s.id,
        project_p=project_p.id,
        project_c=project_c.id,
        project_g=project_g.id,
        project_s=project_s.id,
    )


# ---------------------------------------------------------------------------
# Surface 1: fan-out list, services.project_service.list_projects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("combo", "actor_group_attr", "target_project_attr", "cascade_on_visible"),
    [
        ("1_sibling", "c", "project_s", False),
        ("2_inherit", "p", "project_c", True),
        ("4_no_upward", "g", "project_p", False),
        ("5_deep_inherit", "p", "project_g", True),
    ],
)
async def test_list_projects_cascade_matrix(
    db_session: AsyncSession,
    fixture: _Fixture,
    cascade_enabled: bool,
    combo: str,
    actor_group_attr: str,
    target_project_attr: str,
    cascade_on_visible: bool,
) -> None:
    """``list_projects`` (no explicit ``team_id`` filter, the fan-out branch).

    Reproduces PR 2-A's sibling / inherit / no-upward / deep-inherit combos
    through the actual `core.authz.team_scope_filter` choke-point this PR
    wires `list_projects` onto.
    """
    from services.project_service import list_projects

    user = await make_user(db_session)
    actor_group_id: uuid.UUID = getattr(fixture, actor_group_attr)
    target_project_id: uuid.UUID = getattr(fixture, target_project_attr)
    actor = principal_for(user, team_ids=[actor_group_id])

    rows, total = await list_projects(db_session, actor=actor)
    visible_ids = {row.id for row in rows}

    if cascade_enabled:
        assert (target_project_id in visible_ids) is cascade_on_visible, combo
    else:
        # Flat behaviour regardless of combo: only the actor's own direct
        # group's project is visible.
        assert visible_ids == {fixture.__dict__[f"project_{actor_group_attr}"]}
        assert total == 1


async def test_list_projects_super_admin_bypasses_both_flag_states(
    db_session: AsyncSession, fixture: _Fixture, cascade_enabled: bool
) -> None:
    """super_admin sees every project regardless of the cascade flag,
    the bypass lives in `team_scope_filter` itself (`actor.is_superuser`),
    untouched by whether `project_subtree_predicate` is consulted at all."""
    from services.project_service import list_projects

    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user, team_ids=[], role="super_admin")

    rows, _total = await list_projects(db_session, actor=actor)
    visible_ids = {row.id for row in rows}
    assert {
        fixture.project_p,
        fixture.project_c,
        fixture.project_g,
        fixture.project_s,
    } <= visible_ids


async def test_list_projects_no_membership_sees_nothing(
    db_session: AsyncSession, fixture: _Fixture, cascade_enabled: bool
) -> None:
    """An actor with zero memberships gets an empty page, not every project,
    pins the `sa.false()` / empty-subquery branch in both flag states."""
    from services.project_service import list_projects

    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[])

    rows, total = await list_projects(db_session, actor=actor)
    assert rows == []
    assert total == 0


# ---------------------------------------------------------------------------
# Surface 2: fan-out list, services.scan_service.list_scans_for_actor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("combo", "actor_group_attr", "target_project_attr", "cascade_on_visible"),
    [
        ("1_sibling", "c", "project_s", False),
        ("2_inherit", "p", "project_c", True),
        ("4_no_upward", "g", "project_p", False),
        ("5_deep_inherit", "p", "project_g", True),
    ],
)
async def test_list_scans_for_actor_cascade_matrix(
    db_session: AsyncSession,
    fixture: _Fixture,
    cascade_enabled: bool,
    combo: str,
    actor_group_attr: str,
    target_project_attr: str,
    cascade_on_visible: bool,
) -> None:
    """A second, independently-implemented fan-out surface, same
    `team_scope_filter` choke-point, different join shape (`Scan` joined to
    `Project`), proving the cascade behaviour is the choke-point's, not an
    artifact of one particular query."""
    from models import Project
    from services.scan_service import list_scans_for_actor

    user = await make_user(db_session)
    target_project_id: uuid.UUID = getattr(fixture, target_project_attr)

    # A scan on every project so "is this project's scan visible" pins onto
    # "is this project visible", exactly as the list_projects surface does.
    for attr in ("project_p", "project_c", "project_g", "project_s"):
        project_row = await db_session.get(Project, getattr(fixture, attr))
        assert project_row is not None
        await make_scan(db_session, project=project_row, requested_by=user)

    actor_group_id: uuid.UUID = getattr(fixture, actor_group_attr)
    actor = principal_for(user, team_ids=[actor_group_id])

    scans, _total = await list_scans_for_actor(db_session, actor=actor)
    visible_project_ids = {scan.project_id for scan in scans}

    if cascade_enabled:
        assert (target_project_id in visible_project_ids) is cascade_on_visible, combo
    else:
        assert visible_project_ids == {
            fixture.__dict__[f"project_{actor_group_attr}"]
        }


# ---------------------------------------------------------------------------
# Surface 3: single-resource gate, services.project_service.archive_project
# (a converted "local reimplementation" site, was `_can_access_team`, now
# `core.authz.can_access_group`)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("combo", "actor_group_attr", "target_project_attr", "cascade_on_access"),
    [
        ("1_sibling", "c", "project_s", False),
        ("2_inherit", "p", "project_c", True),
        ("4_no_upward", "g", "project_p", False),
        ("5_deep_inherit", "p", "project_g", True),
    ],
)
async def test_archive_project_cascade_matrix(
    db_session: AsyncSession,
    fixture: _Fixture,
    cascade_enabled: bool,
    combo: str,
    actor_group_attr: str,
    target_project_attr: str,
    cascade_on_access: bool,
) -> None:
    """``archive_project``, single-resource gate via `can_access_group`.

    Unlike the two fan-out surfaces above, this exercises the async,
    session-carrying primitive PR 2-C ADDS (`core.authz.can_access_group`),
    not `team_scope_filter`. A denial raises `ProjectForbidden`; the
    project's `archived_at` must stay `None` either way, the outcome under
    test is authorization, not idempotency.
    """
    from models import Project
    from services.project_service import ProjectForbidden, archive_project

    user = await make_user(db_session)
    actor_group_id: uuid.UUID = getattr(fixture, actor_group_attr)
    target_project_id: uuid.UUID = getattr(fixture, target_project_attr)
    actor = principal_for(user, team_ids=[actor_group_id])

    should_succeed = cascade_on_access if cascade_enabled else (
        actor_group_attr == target_project_attr.removeprefix("project_")
    )

    if should_succeed:
        project = await archive_project(db_session, project_id=target_project_id, actor=actor)
        assert project.archived_at is not None
    else:
        with pytest.raises(ProjectForbidden):
            await archive_project(db_session, project_id=target_project_id, actor=actor)
        row = await db_session.get(Project, target_project_id)
        assert row is not None
        assert row.archived_at is None


async def test_archive_project_super_admin_bypasses_both_flag_states(
    db_session: AsyncSession, fixture: _Fixture, cascade_enabled: bool
) -> None:
    """super_admin may archive any project regardless of the cascade flag,
    `can_access_group`'s own bypass (`core/authz.py`), not
    `group_service.can_access_group`'s (which has none by design)."""
    from services.project_service import archive_project

    user = await make_user(db_session, is_superuser=True)
    actor = principal_for(user, team_ids=[], role="super_admin")

    project = await archive_project(db_session, project_id=fixture.project_s, actor=actor)
    assert project.archived_at is not None


# ---------------------------------------------------------------------------
# Explicit team_id filter branch of list_projects (single-resource-shaped
# access check feeding a still-single-team query), the other half of the
# "6 direct clamp" conversion in `project_service.list_projects`.
# ---------------------------------------------------------------------------


async def test_list_projects_explicit_team_id_cascade(
    db_session: AsyncSession, fixture: _Fixture, cascade_enabled: bool
) -> None:
    """`team_id=fixture.c` explicitly, from an actor who is a direct member
    only of the parent `p`. Cascade ON: access granted (P's membership
    reaches C), result narrowed to exactly C's project. Cascade OFF: 403.
    """
    from services.project_service import ProjectForbidden, list_projects

    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[fixture.p])

    if cascade_enabled:
        rows, total = await list_projects(db_session, actor=actor, team_id=fixture.c)
        assert total == 1
        assert {row.id for row in rows} == {fixture.project_c}
    else:
        with pytest.raises(ProjectForbidden):
            await list_projects(db_session, actor=actor, team_id=fixture.c)


# ---------------------------------------------------------------------------
# services.assignee, the "special-cased" surface (task's dedicated verification).
#
# `list_assignable_members` / `is_assignable_to_team` widen to the target
# group's ANCESTORS (never its descendants) when the cascade flag is on, see
# `services.assignee._cascade_scope_ids`'s docstring for why the direction is
# one-way. Built directly against the tree fixture's own groups (P -> C -> G,
# S a sibling of C) rather than its projects, since these two functions take
# a group id directly and are not project-shaped.
# ---------------------------------------------------------------------------


@pytest.fixture
async def team_members(db_session: AsyncSession, fixture: _Fixture) -> dict[str, uuid.UUID]:
    """One direct member per group in the tree (`p`, `c`, `g`, `s`)."""
    from models import Team

    ids: dict[str, uuid.UUID] = {}
    for attr in ("p", "c", "g", "s"):
        user = await make_user(db_session, full_name=f"Member of {attr}")
        team_id: uuid.UUID = getattr(fixture, attr)
        team_row = await db_session.get(Team, team_id)
        assert team_row is not None
        await make_membership(db_session, user=user, team=team_row)
        ids[attr] = user.id
    return ids


async def test_assignable_members_widens_to_ancestors_only(
    db_session: AsyncSession,
    fixture: _Fixture,
    team_members: dict[str, uuid.UUID],
    cascade_enabled: bool,
) -> None:
    from services.assignee import list_assignable_members

    rows = await list_assignable_members(db_session, fixture.c)
    visible_user_ids = {row[0] for row in rows}

    # C's own direct member is always assignable, in both flag states.
    assert team_members["c"] in visible_user_ids

    # G is a DESCENDANT of C, its member must never appear, regardless of
    # the flag (cascade access flows down from an ancestor's membership, an
    # inherited assignee-eligibility never flows up from a descendant's).
    assert team_members["g"] not in visible_user_ids

    # S is a SIBLING of C (not an ancestor), its member must never appear.
    assert team_members["s"] not in visible_user_ids

    if cascade_enabled:
        # P is C's ANCESTOR, cascade ON widens the picker to P's direct
        # member, because that member can already cascade-READ C's project
        # through `core.authz.team_scope_filter` once a later phase turns
        # this on for `get_project` too.
        assert team_members["p"] in visible_user_ids
    else:
        # Flag off: byte-for-byte today's behaviour, exactly C's own
        # direct members, nobody else.
        assert visible_user_ids == {team_members["c"]}


async def test_is_assignable_to_team_matches_the_list(
    db_session: AsyncSession,
    fixture: _Fixture,
    team_members: dict[str, uuid.UUID],
    cascade_enabled: bool,
) -> None:
    """The write-time check and the picker never drift, a name the list
    offers is a name the write accepts, and vice versa, in both flag states."""
    from services.assignee import is_assignable_to_team

    assert await is_assignable_to_team(db_session, team_members["c"], fixture.c) is True
    assert await is_assignable_to_team(db_session, team_members["g"], fixture.c) is False
    assert await is_assignable_to_team(db_session, team_members["s"], fixture.c) is False
    assert (
        await is_assignable_to_team(db_session, team_members["p"], fixture.c)
        is cascade_enabled
    )
