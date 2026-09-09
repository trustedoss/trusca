# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group-hierarchy permission cascade — Phase 2 PR 2-A.

Covers ``services.group_service`` in isolation: nothing here goes through
``core/authz.py`` or any route (this PR does not wire the cascade in —
that is PR 2-C). Every test builds a real group tree against Postgres via
``parent_group_id`` (Phase 1, migrations 0090/0091) and reads back the
DB-trigger-derived ``path``, rather than hand-constructing an ancestor list
in Python — the point is to prove the algorithm against what the trigger
actually produces, not against a stand-in.

Five depth x permission combinations (the task's original four, plus a 5th
added after security review found none of the four exercised an ancestor
walk deeper than one level):

  1. sibling  — admin at C, target is C's sibling S. No access, no role.
     This is the CWE-863 invariant: a sibling branch is never consulted.
  2. inherit  — admin at parent P, target is child C with no membership of
     its own. Role inherited from P; access granted.
  3. override — admin at P AND viewer at C directly, target is C. C's own
     (lower) role wins over P's (higher) one — a demotion is inherited
     exactly like a promotion is, because both are just "nearest wins".
  4. no-upward — admin at grandchild G, target is grandparent P (G's own
     ancestor). No access, no role — inheritance runs one direction only.
  5. deep-inherit — admin at P only, target is grandchild G (P -> C -> G,
     neither C nor G has a membership of its own). Role/access must still
     reach through both intervening levels. Combos 1-4 above all use a
     target whose ancestor path has length <= 1, so a bug that only
     consulted the immediate parent (instead of walking the whole path)
     would have passed every one of them; this combo is the one that
     distinguishes "walks the full ancestor chain" from "checks one level
     up" (security review, PR 2-A).

Each combination runs under both ``GROUP_CASCADE_ENABLED`` states:

  - ``can_access_group`` reads the flag itself (see its docstring), so the
    off-state assertion below exercises that branch directly: only a group
    with a genuine direct membership is accessible, nothing wider.
  - ``effective_role_at`` does NOT read the flag (by design — see its
    docstring: unconditional algorithm, so turning the cascade on is a pure
    configuration change and not a second code path). What differs between
    "on" and "off" for that function is what its caller would populate
    ``direct_roles`` with once PR 2-C wires it in: an off-cascade caller has
    no reason to look up anything beyond the exact group being checked, so
    the "off" half of each ``effective_role_at`` case below passes a
    ``direct_roles`` limited to the target group's own id, simulating that
    future caller, and confirms the walk degrades to today's flat lookup
    when given that input.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_team

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
# Tree fixture: P (root) -> C (child of P) -> G (grandchild of P via C)
#                      \-> S (sibling of C, also a child of P)
# ---------------------------------------------------------------------------


class _Tree:
    def __init__(self, p: uuid.UUID, c: uuid.UUID, g: uuid.UUID, s: uuid.UUID) -> None:
        self.p = p
        self.c = c
        self.g = g
        self.s = s


@pytest.fixture
async def tree(db_session: AsyncSession) -> _Tree:
    org = await make_organization(db_session)
    p = await make_team(db_session, organization=org)
    c = await make_team(db_session, organization=org, parent=p)
    g = await make_team(db_session, organization=org, parent=c)
    s = await make_team(db_session, organization=org, parent=p)
    return _Tree(p=p.id, c=c.id, g=g.id, s=s.id)


async def _group_path(session: AsyncSession, group_id: uuid.UUID) -> list[uuid.UUID]:
    from models.auth import Group

    row = (await session.execute(select(Group.path).where(Group.id == group_id))).scalar_one()
    return list(row)


# ---------------------------------------------------------------------------
# can_access_group — flag-gated at the function itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("combo", "actor_group_attr", "target_group_attr", "cascade_on_access"),
    [
        ("1_sibling", "c", "s", False),
        ("2_inherit", "p", "c", True),
        ("3_override", "c", "c", True),  # direct membership at the target itself
        ("4_no_upward", "g", "p", False),
        ("5_deep_inherit", "p", "g", True),  # depth-2: P's membership reaches grandchild G
    ],
)
async def test_can_access_group_matrix(
    db_session: AsyncSession,
    tree: _Tree,
    cascade_enabled: bool,
    combo: str,
    actor_group_attr: str,
    target_group_attr: str,
    cascade_on_access: bool,
) -> None:
    from services.group_service import can_access_group

    actor_group_id: uuid.UUID = getattr(tree, actor_group_attr)
    target_group_id: uuid.UUID = getattr(tree, target_group_attr)
    direct_group_ids = [actor_group_id]

    result = await can_access_group(db_session, direct_group_ids, target_group_id)

    if cascade_enabled:
        assert result is cascade_on_access
    else:
        # Flat behaviour: accessible iff the target IS the actor's own direct
        # membership group. Every combo here where actor != target must be
        # denied off; combo 3 has actor == target ("c"/"c") and must still be
        # granted off, because that is a genuine direct membership, not an
        # inherited one.
        assert result is (actor_group_id == target_group_id)


async def test_can_access_group_nonexistent_group_is_false(
    db_session: AsyncSession, tree: _Tree, cascade_enabled: bool
) -> None:
    from services.group_service import can_access_group

    assert await can_access_group(db_session, [tree.p], uuid.uuid4()) is False


# ---------------------------------------------------------------------------
# effective_role_at — unconditional algorithm; the flag lives in what the
# (future) caller passes as `direct_roles`, simulated explicitly below.
# ---------------------------------------------------------------------------


async def _direct_roles_for(
    db_session: AsyncSession,
    *,
    tree: _Tree,
    memberships: Mapping[str, str],
    cascade_enabled: bool,
    target_group_id: uuid.UUID,
) -> dict[uuid.UUID, str]:
    """Build the ``direct_roles`` mapping a caller of ``effective_role_at``
    would pass, for a given flag state.

    ``memberships`` maps a tree attribute name ("p", "c", "g", "s") to a
    role — the actor's real direct memberships, unconditionally on the flag
    (the flag does not change what rows exist in ``memberships`` in
    production either). What differs is which of those rows a flag-aware
    caller bothers to look up before calling this pure function: cascade ON
    looks up every one of the actor's memberships (any of them might be an
    ancestor of the target); cascade OFF only ever needs the target's own
    id, matching today's flat query (``SELECT role FROM memberships WHERE
    group_id = :target``).
    """
    full = {getattr(tree, attr): role for attr, role in memberships.items()}
    if cascade_enabled:
        return full
    return {gid: role for gid, role in full.items() if gid == target_group_id}


@pytest.mark.parametrize(
    (
        "combo",
        "memberships",
        "target_group_attr",
        "cascade_on_role",
        "flat_role",
    ),
    [
        ("1_sibling", {"c": "group_admin"}, "s", None, None),
        ("2_inherit", {"p": "group_admin"}, "c", "group_admin", None),
        ("3_override", {"p": "group_admin", "c": "viewer"}, "c", "viewer", "viewer"),
        ("4_no_upward", {"g": "group_admin"}, "p", None, None),
        # depth-2: P's membership reaches grandchild G through two intervening
        # levels (P -> C -> G), neither of which has a membership of its own.
        # This is the combo a "only consult the immediate parent" bug would
        # still pass at depth 1 but fail here — see the security review that
        # flagged combos 1-4 as insufficient to distinguish that mutation.
        ("5_deep_inherit", {"p": "group_admin"}, "g", "group_admin", None),
    ],
)
async def test_effective_role_at_matrix(
    db_session: AsyncSession,
    tree: _Tree,
    cascade_enabled: bool,
    combo: str,
    memberships: Mapping[str, str],
    target_group_attr: str,
    cascade_on_role: str | None,
    flat_role: str | None,
) -> None:
    from services.group_service import effective_role_at

    target_group_id: uuid.UUID = getattr(tree, target_group_attr)
    group_path = await _group_path(db_session, target_group_id)
    direct_roles = await _direct_roles_for(
        db_session,
        tree=tree,
        memberships=memberships,
        cascade_enabled=cascade_enabled,
        target_group_id=target_group_id,
    )

    result = effective_role_at(direct_roles, group_path, target_group_id)

    expected = cascade_on_role if cascade_enabled else flat_role
    assert result == expected


# ---------------------------------------------------------------------------
# subtree_roots — identity in both flag states (see its docstring: it never
# expands; that happens in subtree_scope_filter's predicate instead).
# ---------------------------------------------------------------------------


def test_subtree_roots_is_identity_regardless_of_flag(
    tree: _Tree, cascade_enabled: bool
) -> None:
    from services.group_service import subtree_roots

    ids = [tree.p, tree.c]
    assert list(subtree_roots(ids)) == ids


def test_subtree_roots_identity_on_empty_input(cascade_enabled: bool) -> None:
    from services.group_service import subtree_roots

    assert list(subtree_roots([])) == []


# ---------------------------------------------------------------------------
# subtree_scope_filter / group_scoped_subquery_predicate — predicate shape
# and the actual DB behaviour it produces (queried through Group directly,
# so this exercises the real predicate against the real tree).
# ---------------------------------------------------------------------------


async def test_subtree_scope_filter_empty_input_matches_nothing(
    db_session: AsyncSession, tree: _Tree, cascade_enabled: bool
) -> None:
    from models.auth import Group
    from services.group_service import subtree_scope_filter

    rows = (
        await db_session.execute(select(Group.id).where(subtree_scope_filter([])))
    ).scalars().all()
    assert rows == []


async def test_subtree_scope_filter_off_is_flat_membership_only(
    db_session: AsyncSession, tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    from models.auth import Group
    from services.group_service import subtree_scope_filter

    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "false")
    rows = (
        await db_session.execute(select(Group.id).where(subtree_scope_filter([tree.p])))
    ).scalars().all()
    assert set(rows) == {tree.p}


async def test_subtree_scope_filter_on_covers_whole_subtree(
    db_session: AsyncSession, tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    from models.auth import Group
    from services.group_service import subtree_scope_filter

    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true")
    rows = (
        await db_session.execute(select(Group.id).where(subtree_scope_filter([tree.p])))
    ).scalars().all()
    # P itself, plus every descendant (C, G, S) — but never a group outside
    # this tree (implicitly proven by the exact-set comparison, not a subset
    # check).
    assert set(rows) == {tree.p, tree.c, tree.g, tree.s}


async def test_subtree_scope_filter_on_does_not_leak_a_sibling_subtree(
    db_session: AsyncSession, tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Membership at C's subtree must not also surface S's subtree — the
    same sibling-isolation invariant as combo 1, expressed at the predicate
    level rather than through ``can_access_group``.
    """
    from models.auth import Group
    from services.group_service import subtree_scope_filter

    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true")
    rows = (
        await db_session.execute(select(Group.id).where(subtree_scope_filter([tree.c])))
    ).scalars().all()
    assert set(rows) == {tree.c, tree.g}
    assert tree.s not in rows
    assert tree.p not in rows


async def test_project_subtree_predicate_scopes_a_real_project(
    db_session: AsyncSession, tree: _Tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    from models.auth import Team
    from models.scan import Project
    from services.group_service import project_subtree_predicate
    from tests._helpers import make_project

    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true")

    group_g = await db_session.get(Team, tree.g)
    group_s = await db_session.get(Team, tree.s)
    assert group_g is not None
    assert group_s is not None
    project_under_grandchild = await make_project(db_session, team=group_g)
    project_under_sibling = await make_project(db_session, team=group_s)

    rows = (
        await db_session.execute(
            select(Project.id).where(project_subtree_predicate([tree.p]))
        )
    ).scalars().all()
    assert project_under_grandchild.id in rows
    assert project_under_sibling.id in rows  # S is also under P's subtree

    rows_scoped_to_c = (
        await db_session.execute(
            select(Project.id).where(project_subtree_predicate([tree.c]))
        )
    ).scalars().all()
    assert project_under_grandchild.id in rows_scoped_to_c
    assert project_under_sibling.id not in rows_scoped_to_c


# ---------------------------------------------------------------------------
# group_cascade_enabled — the flag accessor itself (core/config.py)
# ---------------------------------------------------------------------------


def test_group_cascade_enabled_default_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    # Phase 5: flipped from off to on now that reparent/create_subgroup (and
    # their admin UI) close the gap that made turning this on unsafe -- see
    # group_cascade_enabled's own docstring.
    from core.config import group_cascade_enabled

    monkeypatch.delenv("GROUP_CASCADE_ENABLED", raising=False)
    assert group_cascade_enabled() is True


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("false", False),
        ("0", False),
        ("yes", False),  # only the literal "true" (case-insensitive) is on
        ("", False),
    ],
)
def test_group_cascade_enabled_parses_exact_true_only(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    from core.config import group_cascade_enabled

    monkeypatch.setenv("GROUP_CASCADE_ENABLED", raw)
    assert group_cascade_enabled() is expected
