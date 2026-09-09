# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
``services.group_service.reparent`` / ``create_subgroup`` — group-hierarchy
Phase 5 PR 5-A.

Phase 1 (migrations 0090/0091) built the schema, the ``path``-deriving DB
trigger and ``verify_group_paths()`` specifically so this PR would have
somewhere safe to land the first code path that actually WRITES
``parent_group_id`` after creation. This file exercises that path as a
lifecycle sequence (CLAUDE.md hardening rule 5), not just isolated calls:

  - create -> move -> re-move -> delete-while-has-children (still rejected,
    Phase 1's ``TeamHasChildren``) -> delete after clearing children.
  - cycle detection: self-parent, and moving a group under its own
    descendant.
  - cross-organization moves are refused outright (tenant-isolation
    boundary — see ``GroupCrossOrganizationNotAllowed``'s docstring).
  - sibling slug conflict on ``create_subgroup`` surfaces as a legible 409,
    not a raw ``IntegrityError``.
  - a moved subtree's ``path`` values, cross-checked against
    ``verify_group_paths()`` (Phase 1's own diagnostic), have zero
    mismatches after the move — the direct proof the descendant
    propagation SQL is correct, not just "the moved row itself looks right".
  - a move actually recomputes the permission cascade (Phase 2) and policy
    resolution (Phase 3) for the moved subtree, live, with no separate
    "recompute" step — both walk ``path`` fresh on every call.
  - a move writes an audit_logs row naming the change.

Concurrency (two transactions racing to make each other's group their own
parent) lives in ``test_group_reparent_concurrency.py`` — it needs raw
per-transaction control this file's single-session fixture does not give it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.authz import can_access_group
from schemas.gate_policy import GatePolicyUpsertIn
from schemas.license_policy import LicensePolicyUpsertIn
from services.admin_team_service import TeamHasChildren, delete_team
from services.gate_policy_service import resolve_for_project
from services.gate_policy_service import upsert_team_policy as gate_upsert_team_policy
from services.group_service import (
    GroupCrossOrganizationNotAllowed,
    GroupCycleDetected,
    GroupHierarchyNotFound,
    GroupSlugConflict,
    create_subgroup,
    reparent,
)
from services.license_policy_service import get_effective_policy
from services.license_policy_service import upsert_team_policy as license_upsert_team_policy
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

pytestmark = pytest.mark.integration


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


async def _super_actor(session: AsyncSession):
    admin = await make_user(session, is_superuser=True)
    return principal_for(admin, role="super_admin")


async def _fetch_path(
    session: AsyncSession, group_id: uuid.UUID
) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
    row = (
        await session.execute(
            text("SELECT parent_group_id, path FROM groups WHERE id = :id"),
            {"id": str(group_id)},
        )
    ).first()
    assert row is not None
    return row.parent_group_id, list(row.path)


async def _verify_paths(session: AsyncSession) -> list:
    return list((await session.execute(text("SELECT * FROM verify_group_paths()"))).fetchall())


# ---------------------------------------------------------------------------
# Lifecycle: create -> move -> re-move -> delete (blocked, then cleared)
# ---------------------------------------------------------------------------


async def test_lifecycle_create_move_remove_delete(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)

    root_a = await make_team(db_session, organization=org, name="root-a")
    root_b = await make_team(db_session, organization=org, name="root-b")

    # create: a subgroup nested under root_a.
    child = await create_subgroup(
        db_session,
        actor=actor,
        parent_group_id=root_a.id,
        name="child",
        slug=f"child-{unique_suffix()}",
    )
    parent_id, path = await _fetch_path(db_session, child.id)
    assert parent_id == root_a.id
    assert path == [root_a.id]

    # delete root_a while it still has a child: rejected (Phase 1's
    # TeamHasChildren, now actually reachable for the first time).
    with pytest.raises(TeamHasChildren) as excinfo:
        await delete_team(db_session, actor=actor, team_id=root_a.id)
    assert excinfo.value.extensions.get("team_has_children") is True
    assert excinfo.value.extensions.get("child_count") == 1

    # move: child moves from root_a to root_b.
    moved = await reparent(db_session, actor=actor, group_id=child.id, new_parent_id=root_b.id)
    assert moved.parent_group_id == root_b.id
    parent_id, path = await _fetch_path(db_session, child.id)
    assert parent_id == root_b.id
    assert path == [root_b.id]

    # root_a no longer has children: its own delete now succeeds.
    await delete_team(db_session, actor=actor, team_id=root_a.id)

    # re-move: child moves to root (parent = None).
    moved_again = await reparent(db_session, actor=actor, group_id=child.id, new_parent_id=None)
    assert moved_again.parent_group_id is None
    parent_id, path = await _fetch_path(db_session, child.id)
    assert parent_id is None
    assert path == []

    # delete: child (now childless, root) deletes cleanly.
    await delete_team(db_session, actor=actor, team_id=child.id)


async def test_reparent_to_current_parent_is_a_noop(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    root = await make_team(db_session, organization=org)
    child = await make_team(db_session, organization=org, parent=root)

    result = await reparent(db_session, actor=actor, group_id=child.id, new_parent_id=root.id)

    assert result.parent_group_id == root.id
    _, path = await _fetch_path(db_session, child.id)
    assert path == [root.id]


async def test_reparent_missing_group_raises_not_found(db_session: AsyncSession) -> None:
    actor = await _super_actor(db_session)
    with pytest.raises(GroupHierarchyNotFound):
        await reparent(db_session, actor=actor, group_id=uuid.uuid4(), new_parent_id=None)


async def test_reparent_missing_new_parent_raises_not_found(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    group = await make_team(db_session, organization=org)
    with pytest.raises(GroupHierarchyNotFound):
        await reparent(db_session, actor=actor, group_id=group.id, new_parent_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


async def test_reparent_self_as_own_parent_is_a_cycle(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    group = await make_team(db_session, organization=org)

    with pytest.raises(GroupCycleDetected) as excinfo:
        await reparent(db_session, actor=actor, group_id=group.id, new_parent_id=group.id)
    assert excinfo.value.extensions.get("cycle_detected") is True


async def test_reparent_under_own_descendant_is_a_cycle(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    a = await make_team(db_session, organization=org, name="a")
    b = await make_team(db_session, organization=org, name="b", parent=a)
    c = await make_team(db_session, organization=org, name="c", parent=b)

    # Moving a under its own grandchild c would make a its own ancestor.
    with pytest.raises(GroupCycleDetected) as excinfo:
        await reparent(db_session, actor=actor, group_id=a.id, new_parent_id=c.id)
    assert excinfo.value.extensions.get("cycle_detected") is True

    # a is unaffected: still a root group.
    parent_id, path = await _fetch_path(db_session, a.id)
    assert parent_id is None
    assert path == []


# ---------------------------------------------------------------------------
# Organization boundary
# ---------------------------------------------------------------------------


async def test_reparent_across_organizations_is_refused(db_session: AsyncSession) -> None:
    org_1 = await make_organization(db_session)
    org_2 = await make_organization(db_session)
    actor = await _super_actor(db_session)
    group = await make_team(db_session, organization=org_1)
    other_org_parent = await make_team(db_session, organization=org_2)

    with pytest.raises(GroupCrossOrganizationNotAllowed) as excinfo:
        await reparent(
            db_session, actor=actor, group_id=group.id, new_parent_id=other_org_parent.id
        )
    assert excinfo.value.extensions.get("cross_organization_move") is True

    # Unaffected.
    parent_id, _ = await _fetch_path(db_session, group.id)
    assert parent_id is None


async def test_create_subgroup_across_organizations_is_not_possible(
    db_session: AsyncSession,
) -> None:
    """create_subgroup has no organization_id override at all — the child
    always inherits its parent's organization, so there is no cross-org
    input to reject; this pins that inheritance happens even when the
    caller's own organization differs from the parent's (the actor here is
    a super_admin with no team_ids at all, i.e. not scoped to any org)."""
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    parent = await make_team(db_session, organization=org)

    child = await create_subgroup(
        db_session,
        actor=actor,
        parent_group_id=parent.id,
        name="inherits-org",
        slug=f"inherits-{unique_suffix()}",
    )
    assert child.organization_id == org.id


# ---------------------------------------------------------------------------
# Slug conflict on create_subgroup
# ---------------------------------------------------------------------------


async def test_create_subgroup_sibling_slug_conflict(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    parent = await make_team(db_session, organization=org)
    slug = f"dup-{unique_suffix()}"

    await create_subgroup(
        db_session, actor=actor, parent_group_id=parent.id, name="first", slug=slug
    )
    with pytest.raises(GroupSlugConflict):
        await create_subgroup(
            db_session, actor=actor, parent_group_id=parent.id, name="second", slug=slug
        )


async def test_create_subgroup_missing_parent_raises_not_found(db_session: AsyncSession) -> None:
    actor = await _super_actor(db_session)
    with pytest.raises(GroupHierarchyNotFound):
        await create_subgroup(
            db_session,
            actor=actor,
            parent_group_id=uuid.uuid4(),
            name="orphan",
            slug=f"orphan-{unique_suffix()}",
        )


# ---------------------------------------------------------------------------
# verify_group_paths() zero-mismatch after a subtree move
# ---------------------------------------------------------------------------


async def test_move_of_a_deep_subtree_matches_verify_group_paths(
    db_session: AsyncSession,
) -> None:
    """root_a -> b -> c -> d (depth 4), plus an unrelated root_p. Moving b
    (with its whole subtree c, d) under root_p must leave EVERY row's path
    self-consistent with parent_group_id, per Phase 1's own
    recursive-CTE cross-check — not just "b's own path looks right"."""
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    root_a = await make_team(db_session, organization=org, name="root-a")
    b = await make_team(db_session, organization=org, name="b", parent=root_a)
    c = await make_team(db_session, organization=org, name="c", parent=b)
    d = await make_team(db_session, organization=org, name="d", parent=c)
    root_p = await make_team(db_session, organization=org, name="root-p")

    await reparent(db_session, actor=actor, group_id=b.id, new_parent_id=root_p.id)

    _, path_b = await _fetch_path(db_session, b.id)
    _, path_c = await _fetch_path(db_session, c.id)
    _, path_d = await _fetch_path(db_session, d.id)
    assert path_b == [root_p.id]
    assert path_c == [root_p.id, b.id]
    assert path_d == [root_p.id, b.id, c.id]

    mismatches = await _verify_paths(db_session)
    assert mismatches == [], (
        f"verify_group_paths() reported {len(mismatches)} mismatch(es) after "
        "moving a depth-3 subtree"
    )


# ---------------------------------------------------------------------------
# Permission cascade recompute (Phase 2) — live, no separate step
# ---------------------------------------------------------------------------


async def test_move_recomputes_cascade_access_live(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P and Q are two unrelated roots. C starts under P. p_admin (direct
    member of P only) can reach C while it is under P; after moving C to Q,
    p_admin must LOSE access and q_admin must GAIN it — both re-derived from
    C's ``path`` on every ``can_access_group`` call, with no cache to
    invalidate (services.group_service module docstring)."""
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true")

    org = await make_organization(db_session)
    p = await make_team(db_session, organization=org, name="p")
    q = await make_team(db_session, organization=org, name="q")
    c = await make_team(db_session, organization=org, name="c", parent=p)

    super_actor = await _super_actor(db_session)

    p_user = await make_user(db_session)
    await make_membership(db_session, user=p_user, team=p, role="group_admin")
    p_actor = principal_for(p_user, team_ids=[p.id], role="group_admin")

    q_user = await make_user(db_session)
    await make_membership(db_session, user=q_user, team=q, role="group_admin")
    q_actor = principal_for(q_user, team_ids=[q.id], role="group_admin")

    assert await can_access_group(db_session, p_actor, c.id) is True
    assert await can_access_group(db_session, q_actor, c.id) is False

    await reparent(db_session, actor=super_actor, group_id=c.id, new_parent_id=q.id)

    assert await can_access_group(db_session, p_actor, c.id) is False
    assert await can_access_group(db_session, q_actor, c.id) is True


# ---------------------------------------------------------------------------
# Policy recompute (Phase 3) — live, no separate step
# ---------------------------------------------------------------------------


def _license_payload(**overrides) -> LicensePolicyUpsertIn:
    base: dict = dict(
        name="reparent-policy-test",
        category_overrides={},
        license_exceptions=[],
        unknown_license_category="conditional",
        enabled=True,
    )
    base.update(overrides)
    return LicensePolicyUpsertIn(**base)


async def test_move_recomputes_effective_license_policy(db_session: AsyncSession) -> None:
    """p has an enabled license policy named "p-policy"; q has none. c starts
    under p and resolves to p's policy. After moving c under q, c must
    resolve to... nothing at this org (no org default either) — the point
    being the SAME get_effective_policy call now returns a DIFFERENT answer
    for the same group id, with no policy recompute step of its own."""
    org = await make_organization(db_session)
    super_actor = await _super_actor(db_session)
    p = await make_team(db_session, organization=org, name="p")
    q = await make_team(db_session, organization=org, name="q")
    c = await make_team(db_session, organization=org, name="c", parent=p)

    p_admin = await make_user(db_session)
    await make_membership(db_session, user=p_admin, team=p, role="group_admin")
    p_actor = principal_for(p_admin, team_ids=[p.id], role="group_admin")
    await license_upsert_team_policy(
        db_session, p_actor, team_id=p.id, payload=_license_payload(name="p-policy")
    )

    before = await get_effective_policy(db_session, team_id=c.id)
    assert before is not None
    assert before.team_id == p.id
    assert before.name == "p-policy"

    await reparent(db_session, actor=super_actor, group_id=c.id, new_parent_id=q.id)

    after = await get_effective_policy(db_session, team_id=c.id)
    assert after is None, (
        f"expected no policy to apply to c once under q (no policy at q, no "
        f"org default), got {after!r}"
    )


def _gate_payload(**overrides) -> GatePolicyUpsertIn:
    base: dict = dict(reachable_critical_only=True)
    base.update(overrides)
    return GatePolicyUpsertIn(**base)


async def test_move_recomputes_effective_gate_policy(db_session: AsyncSession) -> None:
    """Same shape as the license-policy test, for the gate-policy resolver
    (a different fall-through implementation — Phase 3 generalised both).
    p sets ``reachable_critical_only``; q has no policy at all. c starts
    under p and resolves the field from p; after moving c under q, the SAME
    resolve call must stop finding it (None, not "stuck at the old value")."""
    org = await make_organization(db_session)
    super_actor = await _super_actor(db_session)
    p = await make_team(db_session, organization=org, name="p")
    q = await make_team(db_session, organization=org, name="q")
    c = await make_team(db_session, organization=org, name="c", parent=p)
    project = await make_project(db_session, team=c)

    p_admin = await make_user(db_session)
    await make_membership(db_session, user=p_admin, team=p, role="group_admin")
    p_actor = principal_for(p_admin, team_ids=[p.id], role="group_admin")
    await gate_upsert_team_policy(
        db_session,
        p_actor,
        team_id=p.id,
        payload=_gate_payload(reachable_critical_only=True),
    )

    before = await resolve_for_project(db_session, project_id=project.id)
    assert before.reachable_critical_only is True

    await reparent(db_session, actor=super_actor, group_id=c.id, new_parent_id=q.id)

    after = await resolve_for_project(db_session, project_id=project.id)
    assert after.reachable_critical_only is None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


async def test_reparent_writes_audit_log_row(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    actor = await _super_actor(db_session)
    group = await make_team(db_session, organization=org)
    new_parent = await make_team(db_session, organization=org)

    await reparent(db_session, actor=actor, group_id=group.id, new_parent_id=new_parent.id)

    rows = (
        await db_session.execute(
            text(
                "SELECT action, diff FROM audit_logs "
                "WHERE target_table = 'groups' AND target_id = :tid"
            ),
            {"tid": str(group.id)},
        )
    ).all()
    assert rows, "expected an audit_logs row for the reparent"
    assert any(
        r.action == "update" and r.diff.get("parent_group_id") == str(new_parent.id)
        for r in rows
    ), f"no update row named the new parent; got {[dict(r._mapping) for r in rows]}"
