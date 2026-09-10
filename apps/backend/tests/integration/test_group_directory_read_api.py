# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group read API, group-hierarchy Phase 4 PR 4-A.

Covers ``services.group_directory_service`` end to end against the real
Postgres (CLAUDE.md core rule #1): enumeration (existence-hide + list
exclusion), the breadcrumb's narrow response shape, cascade on/off parity
for both the fan-out list and the single-resource detail read, direct vs.
inherited members, the 30-day subtree summary's two documented pitfalls
(the ``Scan`` join and the ``audit_logs`` under-count), and the project
list's group_path enrichment (N+1 guard).

Tree, matching the vocabulary the rest of the group-hierarchy test series
uses (``test_group_cascade_list_detail_parity.py`` etc.):

    P (root) -> C (child of P) -> G (grandchild of P via C)
             \\-> S (sibling of C, also a child of P)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from schemas.group import GroupBreadcrumbEntry
from services.group_directory_service import GroupNotFound, get_group_detail
from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
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
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def cascade_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "true")


@pytest.fixture
def cascade_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUP_CASCADE_ENABLED", "false")


class _TeamRef:
    """Minimal stand-in carrying only the ``.id`` `make_project` /
    `make_membership` read.

    Avoids re-fetching a full `Team`/`Group` ORM row per call in this file,
    every helper here already has the id from `_make_tree`.
    """

    def __init__(self, team_id: uuid.UUID) -> None:
        self.id = team_id


class _Tree:
    def __init__(self, *, p: uuid.UUID, c: uuid.UUID, g: uuid.UUID, s: uuid.UUID) -> None:
        self.p = p
        self.c = c
        self.g = g
        self.s = s


async def _make_tree(session: AsyncSession) -> _Tree:
    org = await make_organization(session)
    p = await make_team(session, organization=org)
    c = await make_team(session, organization=org, parent=p)
    g = await make_team(session, organization=org, parent=c)
    s = await make_team(session, organization=org, parent=p)
    return _Tree(p=p.id, c=c.id, g=g.id, s=s.id)


# ---------------------------------------------------------------------------
# Enumeration: existence-hide on detail, exclusion on list
# ---------------------------------------------------------------------------


async def test_detail_404s_for_nonexistent_group(
    db_session: AsyncSession, cascade_on: None
) -> None:
    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[], role="developer")

    with pytest.raises(GroupNotFound):
        await get_group_detail(db_session, actor=actor, group_id=uuid.uuid4())


async def test_detail_404s_uniformly_for_inaccessible_existing_group(
    db_session: AsyncSession, cascade_off: None
) -> None:
    """A real group the actor has no membership in 404s, same exception type
    (and, at the router, the same body) as a nonexistent id."""
    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[], role="developer")

    with pytest.raises(GroupNotFound):
        await get_group_detail(db_session, actor=actor, group_id=tree.p)


async def test_list_omits_inaccessible_groups(db_session: AsyncSession, cascade_off: None) -> None:
    """A group outside the accessible set never appears in the list page,
    not even as a placeholder row."""
    from services.group_directory_service import list_groups

    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    member_user = await make_user(db_session)
    await make_membership(db_session, user=member_user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[], role="developer")

    page = await list_groups(db_session, actor=actor, q=None, parent_id=None)
    ids = {item.id for item in page.items}
    assert tree.p not in ids
    assert tree.c not in ids
    assert page.total == 0


# ---------------------------------------------------------------------------
# Flat search (`q` set): group-hierarchy Phase 6 security review.
#
# Nothing in this file exercised the search branch at all before this PR;
# it went straight from the drill-down mode's own tests above to the
# breadcrumb ones below, even though `q`-set is the OTHER of the two
# documented, mutually-exclusive modes `list_groups` supports. That gap
# mattered once the project-creation combobox turned this into a
# per-keystroke live-search caller.
# ---------------------------------------------------------------------------


async def test_search_matches_by_name_across_the_whole_tree(
    db_session: AsyncSession, cascade_on: None
) -> None:
    """A flat search finds a match regardless of nesting depth, for an actor
    whose accessible set covers it via cascade from a root membership --
    the search term never has to name the branch it lives under."""
    from services.group_directory_service import list_groups

    suffix = unique_suffix()
    org = await make_organization(db_session)
    root = await make_team(db_session, organization=org, name=f"root-{suffix}")
    child = await make_team(db_session, organization=org, name=f"child-{suffix}", parent=root)
    grandchild = await make_team(
        db_session, organization=org, name=f"deepmatch-{suffix}", parent=child
    )
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(root.id), role="developer")
    actor = principal_for(user, team_ids=[root.id], role="developer")

    page = await list_groups(db_session, actor=actor, q=f"deepmatch-{suffix}", parent_id=None)
    ids = {item.id for item in page.items}
    assert grandchild.id in ids
    assert root.id not in ids
    assert child.id not in ids


async def test_search_below_the_floor_returns_empty_with_no_query(
    db_session: AsyncSession, cascade_on: None
) -> None:
    """A 1-character term short-circuits to an empty page before the
    visibility predicate or the ILIKE scan ever runs -- mirrors
    ``test_search_api.py``'s ``test_query_too_short_returns_empty_200`` for
    the same floor-vs-error shape (200/empty, never a 422): the
    project-creation combobox fires this on every keystroke, so a
    validation error mid-type would be wrong UX, not just wrong status."""
    from services.group_directory_service import list_groups

    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[tree.p], role="developer")

    page = await list_groups(db_session, actor=actor, q="a", parent_id=None)
    assert page.items == []
    assert page.total == 0


async def test_search_at_the_floor_matches_normally(
    db_session: AsyncSession, cascade_on: None
) -> None:
    """The other half of the floor contract: exactly
    ``_MIN_SEARCH_QUERY_LEN`` characters still searches for real."""
    from services.group_directory_service import _MIN_SEARCH_QUERY_LEN, list_groups

    suffix = unique_suffix()
    org = await make_organization(db_session)
    target = await make_team(db_session, organization=org, name=f"findme-{suffix}")
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(target.id), role="developer")
    actor = principal_for(user, team_ids=[target.id], role="developer")

    query = f"findme-{suffix}"[:_MIN_SEARCH_QUERY_LEN]
    assert len(query) == _MIN_SEARCH_QUERY_LEN

    page = await list_groups(db_session, actor=actor, q=query, parent_id=None)
    ids = {item.id for item in page.items}
    assert target.id in ids


async def test_search_still_excludes_inaccessible_groups(
    db_session: AsyncSession, cascade_off: None
) -> None:
    """Search doesn't bypass the same existence-hide/exclusion the
    drill-down mode already enforces (test_list_omits_inaccessible_groups
    above) -- it filters through the identical visibility predicate."""
    from services.group_directory_service import list_groups

    suffix = unique_suffix()
    org = await make_organization(db_session)
    hidden = await make_team(db_session, organization=org, name=f"hidden-{suffix}")
    outsider = await make_user(db_session)
    actor = principal_for(outsider, team_ids=[], role="developer")

    page = await list_groups(db_session, actor=actor, q=f"hidden-{suffix}", parent_id=None)
    assert page.items == []
    assert hidden.id not in {item.id for item in page.items}


# ---------------------------------------------------------------------------
# Breadcrumb: narrow response shape
# ---------------------------------------------------------------------------


def test_breadcrumb_entry_field_set_is_exactly_id_name_slug() -> None:
    """Schema-level enforcement: adding a field here is a deliberate,
    reviewed change, not something a call site can smuggle in."""
    assert set(GroupBreadcrumbEntry.model_fields) == {"id", "name", "slug"}


async def test_detail_ancestors_carry_no_extra_fields_on_the_wire(
    db_session: AsyncSession, cascade_on: None
) -> None:
    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[tree.p], role="developer")

    detail = await get_group_detail(db_session, actor=actor, group_id=tree.g)
    payload = detail.model_dump(mode="json")
    assert len(payload["ancestors"]) == 2  # [P, C], not including G itself
    for entry in payload["ancestors"]:
        assert set(entry.keys()) == {"id", "name", "slug"}
    assert payload["ancestors"][0]["id"] == str(tree.p)
    assert payload["ancestors"][1]["id"] == str(tree.c)


async def test_ancestor_breadcrumb_names_an_inaccessible_ancestor_but_its_own_detail_still_404s(
    db_session: AsyncSession, cascade_off: None
) -> None:
    """Locks in a deliberate, reviewed exception to existence-hide (security
    review, Group read API): the breadcrumb is a position indicator, not an
    enumeration primitive.

    The actor's only membership is C, not its parent P. C's own detail is
    reachable (direct membership) and its breadcrumb names P, an ancestor
    the actor cannot otherwise reach at all (cascade never grants upward
    access; this holds with the cascade off here and would hold on too).
    P's own detail still 404s for this actor: seeing P's name in a
    breadcrumb does not make P itself accessible, and does not leak
    anything about P beyond its name/slug (no siblings, members, projects,
    or stats of P are reachable this way).
    """
    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.c), role="developer")
    actor = principal_for(user, team_ids=[tree.c], role="developer")

    detail = await get_group_detail(db_session, actor=actor, group_id=tree.c)
    ancestor_ids = {entry.id for entry in detail.ancestors}
    assert tree.p in ancestor_ids

    with pytest.raises(GroupNotFound):
        await get_group_detail(db_session, actor=actor, group_id=tree.p)


# ---------------------------------------------------------------------------
# Cascade on/off: accessible-set parity for list AND detail
# ---------------------------------------------------------------------------


async def test_cascade_on_widens_both_list_and_detail_to_the_subtree(
    db_session: AsyncSession, cascade_on: None
) -> None:
    from services.group_directory_service import list_groups

    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[tree.p], role="developer")

    # Detail: C and G (descendants of P) are reachable purely through the cascade.
    for gid in (tree.p, tree.c, tree.g):
        await get_group_detail(db_session, actor=actor, group_id=gid)
    # A group in a completely unrelated tree must stay unreachable, the
    # cascade widens to P's own subtree, not to every group in the org.
    other_root_tree = await _make_tree(db_session)
    with pytest.raises(GroupNotFound):
        await get_group_detail(db_session, actor=actor, group_id=other_root_tree.c)

    # List (drill-down): P's direct children are C and S, both accessible
    # purely through the cascade (the actor's only direct membership is P).
    page = await list_groups(db_session, actor=actor, q=None, parent_id=tree.p)
    child_ids = {item.id for item in page.items}
    assert child_ids == {tree.c, tree.s}


async def test_cascade_off_restricts_both_list_and_detail_to_direct_membership(
    db_session: AsyncSession, cascade_off: None
) -> None:
    from services.group_directory_service import list_groups

    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[tree.p], role="developer")

    await get_group_detail(db_session, actor=actor, group_id=tree.p)
    with pytest.raises(GroupNotFound):
        await get_group_detail(db_session, actor=actor, group_id=tree.c)

    # Drill-down into P's children is itself gated: with the cascade off, the
    # actor cannot reach C or S (neither is a direct membership), so the page
    # is empty rather than 404 (list-level existence-hide by omission).
    page = await list_groups(db_session, actor=actor, q=None, parent_id=tree.p)
    assert page.items == []
    assert page.total == 0

    # Root-level listing still surfaces P itself (a direct membership).
    root_page = await list_groups(db_session, actor=actor, q=None, parent_id=None)
    assert {item.id for item in root_page.items} == {tree.p}


# ---------------------------------------------------------------------------
# Members: direct vs inherited, with source attribution
# ---------------------------------------------------------------------------


async def test_members_direct_and_inherited_split_with_nearest_ancestor_wins(
    db_session: AsyncSession, cascade_on: None
) -> None:
    from services.group_directory_service import get_group_members

    tree = await _make_tree(db_session)
    direct_on_c = await make_user(db_session)
    await make_membership(db_session, user=direct_on_c, team=_TeamRef(tree.c), role="developer")

    inherited_from_p = await make_user(db_session)
    await make_membership(db_session, user=inherited_from_p, team=_TeamRef(tree.p), role="viewer")

    # Nearest-ancestor-wins + direct-always-wins: a user with BOTH a direct
    # membership at C and one at the root P is reported ONLY in `direct`
    # (role=developer from C), never duplicated into `inherited`.
    both = await make_user(db_session)
    await make_membership(db_session, user=both, team=_TeamRef(tree.p), role="viewer")
    await make_membership(db_session, user=both, team=_TeamRef(tree.c), role="developer")

    actor = principal_for(direct_on_c, team_ids=[tree.c], role="developer")
    members = await get_group_members(db_session, actor=actor, group_id=tree.c)

    direct_ids = {m.user_id for m in members.direct}
    inherited_ids = {m.user_id for m in members.inherited}
    assert direct_ids == {direct_on_c.id, both.id}
    assert inherited_ids == {inherited_from_p.id}
    assert both.id not in inherited_ids

    inherited_entry = next(m for m in members.inherited if m.user_id == inherited_from_p.id)
    assert inherited_entry.source_group_id == tree.p
    assert inherited_entry.role == "viewer"


async def test_members_inherited_is_empty_when_cascade_off(
    db_session: AsyncSession, cascade_off: None
) -> None:
    """No ancestor membership confers any reach when the cascade is off,
    the inherited list must be empty, not merely filtered."""
    from services.group_directory_service import get_group_members

    tree = await _make_tree(db_session)
    direct_on_c = await make_user(db_session)
    await make_membership(db_session, user=direct_on_c, team=_TeamRef(tree.c), role="developer")
    ancestor_member = await make_user(db_session)
    await make_membership(db_session, user=ancestor_member, team=_TeamRef(tree.p), role="viewer")

    actor = principal_for(direct_on_c, team_ids=[tree.c], role="developer")
    members = await get_group_members(db_session, actor=actor, group_id=tree.c)

    assert members.inherited == []


# ---------------------------------------------------------------------------
# 30-day summary: the two documented pitfalls
# ---------------------------------------------------------------------------


async def test_summary_scan_count_covers_the_whole_subtree_not_just_this_group(
    db_session: AsyncSession, cascade_on: None
) -> None:
    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[tree.p], role="developer")

    project_p = await make_project(db_session, team=_TeamRef(tree.p))
    project_g = await make_project(db_session, team=_TeamRef(tree.g))
    await make_scan(db_session, project=project_p)
    await make_scan(db_session, project=project_g)
    # Outside the subtree entirely, must not be counted.
    other_tree = await _make_tree(db_session)
    project_other = await make_project(db_session, team=_TeamRef(other_tree.p))
    await make_scan(db_session, project=project_other)

    detail = await get_group_detail(db_session, actor=actor, group_id=tree.p)
    assert detail.stats.subtree_scan_count == 2


async def test_summary_approvals_processed_reads_component_approval_not_audit_log(
    db_session: AsyncSession, cascade_on: None
) -> None:
    """The known gap: component_approval_service never binds group_id into the
    audit context, so a count sourced from audit_logs would under-report.
    This asserts the summary is non-zero even though no audit_logs row for
    the decision carries group_id."""
    from models import Component
    from models.component_approval import ApprovalStatus, ComponentApproval

    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[tree.p], role="developer")

    project_c = await make_project(db_session, team=_TeamRef(tree.c))
    suffix = unique_suffix()
    component = Component(purl=f"pkg:npm/pkg-{suffix}", package_type="npm", name=f"pkg-{suffix}")
    db_session.add(component)
    await db_session.commit()
    await db_session.refresh(component)

    approval = ComponentApproval(
        component_id=component.id,
        project_id=project_c.id,
        team_id=tree.c,
        status=ApprovalStatus.approved,
        decided_at=datetime.now(tz=UTC),
    )
    db_session.add(approval)
    await db_session.commit()

    detail = await get_group_detail(db_session, actor=actor, group_id=tree.p)
    assert detail.stats.subtree_approvals_processed_count == 1


async def test_summary_approvals_outside_window_are_excluded(
    db_session: AsyncSession, cascade_on: None
) -> None:
    from models import Component
    from models.component_approval import ApprovalStatus, ComponentApproval

    tree = await _make_tree(db_session)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=_TeamRef(tree.p), role="developer")
    actor = principal_for(user, team_ids=[tree.p], role="developer")

    project_p = await make_project(db_session, team=_TeamRef(tree.p))
    suffix = unique_suffix()
    component = Component(purl=f"pkg:npm/pkg-{suffix}", package_type="npm", name=f"pkg-{suffix}")
    db_session.add(component)
    await db_session.commit()
    await db_session.refresh(component)

    stale = ComponentApproval(
        component_id=component.id,
        project_id=project_p.id,
        team_id=tree.p,
        status=ApprovalStatus.rejected,
        decided_at=datetime.now(tz=UTC) - timedelta(days=45),
    )
    db_session.add(stale)
    await db_session.commit()

    detail = await get_group_detail(db_session, actor=actor, group_id=tree.p)
    assert detail.stats.subtree_approvals_processed_count == 0


async def test_summary_new_member_count_is_a_subtree_aggregate(
    db_session: AsyncSession, cascade_on: None
) -> None:
    tree = await _make_tree(db_session)
    root_member = await make_user(db_session)
    await make_membership(db_session, user=root_member, team=_TeamRef(tree.p), role="developer")
    grandchild_member = await make_user(db_session)
    await make_membership(db_session, user=grandchild_member, team=_TeamRef(tree.g), role="viewer")
    actor = principal_for(root_member, team_ids=[tree.p], role="developer")

    detail = await get_group_detail(db_session, actor=actor, group_id=tree.p)
    # root_member (on P) + grandchild_member (on G, a descendant of P).
    assert detail.stats.subtree_new_member_count == 2

    child_detail = await get_group_detail(db_session, actor=actor, group_id=tree.c)
    # C's own subtree is {C, G}, root_member's membership (on P) is outside it.
    assert child_detail.stats.subtree_new_member_count == 1


# ---------------------------------------------------------------------------
# N+1 guard: project list group_path enrichment
# ---------------------------------------------------------------------------


async def test_project_group_path_enrichment_is_batched_not_per_row(
    db_session: AsyncSession, cascade_off: None
) -> None:
    """Statement count for `_group_path_map` must not grow with the number of
    distinct owning groups on the page, two batched IN queries, always."""
    from services.project_list_enrichment import _group_path_map

    tree = await _make_tree(db_session)
    projects = [
        await make_project(db_session, team=_TeamRef(tree.p)),
        await make_project(db_session, team=_TeamRef(tree.c)),
        await make_project(db_session, team=_TeamRef(tree.g)),
        await make_project(db_session, team=_TeamRef(tree.s)),
    ]

    engine = db_session.get_bind()
    sync_engine = getattr(engine, "sync_engine", engine)
    counter = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        counter["n"] += 1

    # The single-project case uses G specifically (not P): G has a non-empty
    # `path`, so both the "1 project" and "4 project" runs exercise the SAME
    # two-query shape (leaf lookup + ancestor-name lookup). Comparing against
    # P instead would conflate "fewer ancestors to resolve" with "more
    # projects", which is not the growth this test is pinning.
    g_project = projects[2]
    event.listen(sync_engine, "before_cursor_execute", _count)
    try:
        counter["n"] = 0
        await _group_path_map(db_session, projects=[g_project])
        one_project_stmts = counter["n"]

        counter["n"] = 0
        chains = await _group_path_map(db_session, projects=projects)
        four_project_stmts = counter["n"]
    finally:
        event.remove(sync_engine, "before_cursor_execute", _count)

    assert one_project_stmts == four_project_stmts
    assert four_project_stmts <= 2

    # Correctness: G's chain is [P, C, G] root-first, group itself last.
    chain_ids = [entry.id for entry in chains[g_project.id]]
    assert chain_ids == [tree.p, tree.c, tree.g]
