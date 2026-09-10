# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Group read-API service — group-hierarchy Phase 4 PR 4-A.

The first user-facing (non-admin) read surface over ``groups``. Everything
that reached a group before this PR went through ``/v1/admin/teams``
(super_admin only). This module answers three questions for an ordinary,
group-scoped caller:

  - ``list_groups``       — which groups can I see, flat-searched or
    drilled-down one level at a time?
  - ``get_group_detail``  — one group's own info + its ancestor breadcrumb +
    a 30-day subtree activity summary.
  - ``get_group_members`` — who is on this group directly, and who reaches
    it only through the cascade (with which ancestor granted it)?

Two design points carried over from the rest of the group-hierarchy work,
worth restating here because this is the first read surface built ON TOP of
it rather than inside it:

  - Existence-hide (404, never 403), for PROBING. Unlike
    ``project_service.get_project`` (which 403s — team membership is itself
    a signal there, see that function's own comment), this module 404s
    uniformly whether a group id is nonexistent or simply not in the
    caller's accessible set. Climbing 403s vs 404s across ``parent_id``
    probes (or guessed ids on ``GET /{group_id}``) would map out an org
    chart the caller has no business seeing, so an id the caller did not
    already know about never confirms its own existence this way.

    This is deliberately narrower than "the caller can never learn an
    inaccessible group's name." A caller's OWN ancestor chain is the one
    exception: ``get_group_detail``'s breadcrumb (``ancestors``, below) and
    a project's ``group_path`` both show the name/slug of every ancestor up
    to the root, even one the caller's own accessible set excludes (no
    membership on that ancestor, cascade off, or an ancestor outside the
    subtree the cascade widens into). That is a position indicator — "this
    is what I am nested under" — not an enumeration primitive: it reveals
    nothing about that ancestor's siblings, members, projects or stats, and
    it does not help a probe on an id the caller did not already learn this
    way. ``GET /{that_ancestor_id}`` still 404s for the caller; the UI's
    breadcrumb link therefore may lead to a 404 for a name the caller just
    saw, which is intentional, not a bug (see
    ``test_ancestor_breadcrumb_names_an_inaccessible_ancestor_but_its_own_detail_still_404s``).
  - The 30-day summary is a SUBTREE aggregate, always, in both
    ``GROUP_CASCADE_ENABLED`` states. That flag governs whether an actor's
    OWN accessible set widens past direct membership; it says nothing about
    what "this group's activity" means once the caller is already looking
    at one group's detail page. See ``_subtree_predicate`` below for why it
    deliberately does NOT consult the flag, unlike every predicate in
    ``services.group_service``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from core.authz import can_access_group
from core.config import group_cascade_enabled
from core.security import CurrentUser
from core.sql_safety import escape_like
from models import ComponentApproval, Membership, Project, Scan, User
from models.auth import Group
from schemas.group import (
    GroupBreadcrumbEntry,
    GroupDetail,
    GroupInheritedMemberEntry,
    GroupListItem,
    GroupListPage,
    GroupMemberEntry,
    GroupMembersResponse,
    GroupSummaryStats,
)
from services import group_service

log = structlog.get_logger("groups.directory.service")

#: Mirrors core.pagination.MAX_PAGE_SIZE — kept local so this module has no
#: dependency on a caller picking the right constant.
_MAX_PAGE_SIZE = 200

#: The trailing window every GroupSummaryStats aggregate covers.
_SUMMARY_WINDOW_DAYS = 30

#: group-hierarchy Phase 6 security review: a query shorter than this yields
#: an empty result rather than running the ILIKE scan below, mirroring
#: services.search_service.MIN_QUERY_LEN's soft-fail shape (empty results,
#: not a 422) so the project-creation combobox's live-typing UX stays
#: identical to the ⌘K search box's -- no validation error flashing mid-type.
#: Unlike that constant, this isn't tied to a trigram index floor (`groups`
#: has none, and does not need one at this table's realistic scale); it
#: exists to bound the cost of the shortest, least-selective possible scans
#: now that a per-keystroke caller (TeamCombobox.tsx) hits this endpoint,
#: alongside the new per-actor rate limit (core.config.group_search_rate_limit).
_MIN_SEARCH_QUERY_LEN = 2


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class GroupError(Exception):
    status_code: int = 400
    title: str = "Group Error"


class GroupNotFound(GroupError):
    """404, existence-hidden. See module docstring for why 404 (not 403) here."""

    status_code = 404
    title = "Group Not Found"


# ---------------------------------------------------------------------------
# Predicates
# ---------------------------------------------------------------------------


def _actor_visibility_predicate(actor: CurrentUser) -> ColumnElement[bool]:
    """"Group.id is in the actor's accessible set", for a query selecting
    FROM groups directly (list / search / drill-down).

    Mirrors ``core.authz.team_scope_filter``'s shape and its super-admin
    bypass rationale (gated on ``is_superuser`` alone, not the derived
    ``role == "super_admin"`` string — see that function's docstring for
    the security-review finding this avoids re-opening). The member branch
    delegates to ``group_service.subtree_scope_filter``, which already
    targets ``Group.id`` (unlike ``project_subtree_predicate``, there is no
    extra join needed here — a group query IS the ``groups`` table).
    """
    if actor.is_superuser:
        return sa.true()
    return group_service.subtree_scope_filter(actor.team_ids)


def _subtree_predicate(group_id: uuid.UUID) -> ColumnElement[bool]:
    """"Group.id is group_id itself, or a descendant of it" — unconditional.

    Deliberately does NOT consult ``core.config.group_cascade_enabled``,
    unlike every predicate in ``services.group_service``. Those predicates
    answer an actor-PERMISSION question ("does my membership reach this
    group?") that the flag is the whole point of gating. This predicate
    answers a structural question about the tree itself ("which groups sit
    under this one, that the caller is already looking at?") that has
    nothing to do with how memberships cascade — the 30-day summary always
    means "this group's whole branch", flag on or off.

    Same operator ``subtree_scope_filter``'s cascade-ON branch uses
    (``path && ARRAY[...]``, GIN-index-backed — see that function's
    docstring for the measured query plan and why ``= ANY(path)`` is the
    wrong shape here), applied to a single id.
    """
    return sa.or_(Group.id == group_id, Group.path.op("&&")([group_id]))


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def _batched_count_map(
    session: AsyncSession,
    *,
    column: InstrumentedAttribute[uuid.UUID] | InstrumentedAttribute[uuid.UUID | None],
    group_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """``{id: COUNT(*)}`` grouping whatever table owns *column* by *group_ids*.

    One ``GROUP BY`` query for the whole page — never one COUNT per row.
    Used for all three list-row badges (child groups / projects / members).
    The FROM clause is inferred from *column* itself (it is always a mapped
    class's own column), so no separate ``select_from`` is needed.
    """
    if not group_ids:
        return {}
    stmt = (
        select(column.label("gid"), func.count().label("n"))
        .where(column.in_(group_ids))
        .group_by(column)
    )
    result = await session.execute(stmt)
    return {row.gid: int(row.n) for row in result.all()}


async def list_groups(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    q: str | None = None,
    parent_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 50,
) -> GroupListPage:
    """List groups visible to *actor*, in one of two mutually exclusive modes.

    - ``q`` set (flat search): every accessible group whose name matches,
      regardless of nesting depth or ``parent_id``. ``parent_id`` is
      ignored in this mode — a search result must not force the caller to
      already know which branch a match lives under.
    - ``q`` unset (drill-down): ``parent_id is None`` returns accessible
      root groups; a concrete ``parent_id`` returns that group's accessible
      DIRECT children only. An inaccessible or nonexistent ``parent_id``
      yields an empty page rather than a 404 — the caller cannot query this
      way and learn a parent id exists (existence-hide by omission, same as
      the row-level exclusion below).

    Enumeration: an inaccessible group is simply absent from ``items`` — no
    row, no count contribution, no distinguishing signal from "does not
    exist". Both search and drill-down filter through
    ``_actor_visibility_predicate`` before anything else runs.

    A non-empty ``q`` shorter than :data:`_MIN_SEARCH_QUERY_LEN` yields an
    empty page with no query at all -- see that constant's own docstring.
    """
    page = max(page, 1)
    page_size = max(min(page_size, _MAX_PAGE_SIZE), 1)

    stripped_q = q.strip() if q else ""
    if stripped_q and len(stripped_q) < _MIN_SEARCH_QUERY_LEN:
        return GroupListPage(items=[], total=0, page=page, page_size=page_size)

    visibility = _actor_visibility_predicate(actor)
    base = select(Group).where(visibility)
    count_base = select(func.count()).select_from(Group).where(visibility)

    if stripped_q:
        like = f"%{escape_like(stripped_q)}%"
        base = base.where(Group.name.ilike(like, escape="\\"))
        count_base = count_base.where(Group.name.ilike(like, escape="\\"))
    elif parent_id is None:
        base = base.where(Group.parent_group_id.is_(None))
        count_base = count_base.where(Group.parent_group_id.is_(None))
    else:
        base = base.where(Group.parent_group_id == parent_id)
        count_base = count_base.where(Group.parent_group_id == parent_id)

    total = int((await session.execute(count_base)).scalar_one())
    rows_stmt = (
        base.order_by(Group.name.asc(), Group.id.asc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    groups = list((await session.execute(rows_stmt)).scalars().all())

    group_ids = [g.id for g in groups]
    # Three batched GROUP BY queries over the page's ids — no per-row query.
    child_counts = await _batched_count_map(
        session, column=Group.parent_group_id, group_ids=group_ids
    )
    project_counts = await _batched_count_map(
        session, column=Project.group_id, group_ids=group_ids
    )
    member_counts = await _batched_count_map(
        session, column=Membership.group_id, group_ids=group_ids
    )

    items = [
        GroupListItem(
            id=g.id,
            name=g.name,
            slug=g.slug,
            description=g.description,
            parent_group_id=g.parent_group_id,
            child_group_count=child_counts.get(g.id, 0),
            project_count=project_counts.get(g.id, 0),
            member_count=member_counts.get(g.id, 0),
            updated_at=g.updated_at,
        )
        for g in groups
    ]
    return GroupListPage(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


async def _load_accessible_group(
    session: AsyncSession, *, actor: CurrentUser, group_id: uuid.UUID
) -> Group:
    """The group, if it exists AND *actor* can reach it — else GroupNotFound.

    Same message for "does not exist" and "exists but not reachable"
    (existence-hide, per the module docstring) — the caller never learns
    which case it was.
    """
    group = (
        await session.execute(select(Group).where(Group.id == group_id))
    ).scalar_one_or_none()
    if group is None or not await can_access_group(session, actor, group_id):
        raise GroupNotFound(f"group {group_id} not found")
    return group


async def _ancestor_breadcrumbs(
    session: AsyncSession, *, group: Group
) -> list[GroupBreadcrumbEntry]:
    """Root-first ``[{id, name, slug}, ...]`` for *group*'s own ``path``.

    One batched IN query for the whole ancestor chain (never one query per
    hop) — ``group.path`` already carries the ordered ids, this only needs
    their names/slugs.
    """
    if not group.path:
        return []
    rows = (
        await session.execute(
            select(Group.id, Group.name, Group.slug).where(Group.id.in_(group.path))
        )
    ).all()
    by_id = {r.id: r for r in rows}
    out: list[GroupBreadcrumbEntry] = []
    for ancestor_id in group.path:
        row = by_id.get(ancestor_id)
        if row is None:
            # An ancestor row vanished between the trigger deriving `path`
            # and this read (should not happen — parent_group_id is
            # ON DELETE RESTRICT — but a torn read must not 500 a detail
            # page over one missing breadcrumb hop).
            continue
        out.append(GroupBreadcrumbEntry(id=row.id, name=row.name, slug=row.slug))
    return out


async def _own_counts(session: AsyncSession, *, group_id: uuid.UUID) -> tuple[int, int, int]:
    """``(child_group_count, project_count, member_count)`` for one group — direct only."""
    child_count = (
        await session.execute(
            select(func.count())
            .select_from(Group)
            .where(Group.parent_group_id == group_id)
        )
    ).scalar_one()
    project_count = (
        await session.execute(
            select(func.count()).select_from(Project).where(Project.group_id == group_id)
        )
    ).scalar_one()
    member_count = (
        await session.execute(
            select(func.count()).select_from(Membership).where(Membership.group_id == group_id)
        )
    ).scalar_one()
    return int(child_count), int(project_count), int(member_count)


async def _subtree_group_ids(session: AsyncSession, *, group_id: uuid.UUID) -> list[uuid.UUID]:
    stmt = select(Group.id).where(_subtree_predicate(group_id))
    ids = list((await session.execute(stmt)).scalars().all())
    # Defensive: `group_id` always matches its own `Group.id == group_id`
    # branch, so this is never actually empty for a group that itself
    # exists. Kept explicit so a future refactor of `_subtree_predicate`
    # cannot silently turn "no rows" into "empty summary" instead of
    # "this group's own activity only".
    if group_id not in ids:
        ids.append(group_id)
    return ids


async def _group_summary_stats(
    session: AsyncSession, *, group_id: uuid.UUID
) -> GroupSummaryStats:
    """30-day subtree activity summary. See the module docstring's two
    pitfalls this specifically avoids (the Scan join and the audit-log
    under-count) — restated at each query below.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=_SUMMARY_WINDOW_DAYS)
    subtree_ids = await _subtree_group_ids(session, group_id=group_id)

    # Scan carries no group column — it only has project_id. Reaching "every
    # scan in this group's SUBTREE" needs the two-step join
    # groups -> projects -> scans, not a direct filter on Scan.
    scan_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Scan)
                .join(Project, Project.id == Scan.project_id)
                .where(Project.group_id.in_(subtree_ids), Scan.created_at >= cutoff)
            )
        ).scalar_one()
    )

    # NOT audit_logs.group_id: component_approval_service.py never calls
    # bind_audit_team on its decision path, so an approval's audit row (if
    # one exists at all) carries no group_id and this count would silently
    # under-report. ComponentApproval.team_id is written directly on the
    # domain row itself and is authoritative.
    approvals_processed = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ComponentApproval)
                .where(
                    ComponentApproval.team_id.in_(subtree_ids),
                    ComponentApproval.decided_at.is_not(None),
                    ComponentApproval.decided_at >= cutoff,
                )
            )
        ).scalar_one()
    )

    new_members = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Membership)
                .where(Membership.group_id.in_(subtree_ids), Membership.created_at >= cutoff)
            )
        ).scalar_one()
    )

    return GroupSummaryStats(
        window_days=_SUMMARY_WINDOW_DAYS,
        subtree_scan_count=scan_count,
        subtree_approvals_processed_count=approvals_processed,
        subtree_new_member_count=new_members,
    )


async def get_group_detail(
    session: AsyncSession, *, actor: CurrentUser, group_id: uuid.UUID
) -> GroupDetail:
    group = await _load_accessible_group(session, actor=actor, group_id=group_id)
    ancestors = await _ancestor_breadcrumbs(session, group=group)
    child_count, project_count, member_count = await _own_counts(session, group_id=group.id)
    stats = await _group_summary_stats(session, group_id=group.id)

    return GroupDetail(
        id=group.id,
        name=group.name,
        slug=group.slug,
        description=group.description,
        parent_group_id=group.parent_group_id,
        ancestors=ancestors,
        child_group_count=child_count,
        project_count=project_count,
        member_count=member_count,
        stats=stats,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


async def _inherited_members(
    session: AsyncSession, *, group: Group, direct_user_ids: set[uuid.UUID]
) -> list[GroupInheritedMemberEntry]:
    """Ancestor memberships that reach *group* through the cascade.

    Empty whenever ``group_cascade_enabled()`` is off, or *group* has no
    ancestors — see the module docstring for why this does not itself
    re-derive that from ``can_access_group`` per row: with the cascade off,
    no ancestor membership grants access to a descendant at all, so this
    list must be empty exactly then, unconditionally, not "empty unless
    some other rule says otherwise".

    Nearest-ancestor-wins (mirrors ``services.group_service.
    effective_role_at``): a user with direct memberships at more than one
    ancestor is attributed to the NEAREST one only, and a user who is
    already a DIRECT member of *group* itself is excluded entirely — a
    direct membership at the group always wins over any inherited one,
    never both.
    """
    if not group_cascade_enabled() or not group.path:
        return []

    ancestor_ids = list(group.path)  # root-first
    rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.group_id.in_(ancestor_ids))
        )
    ).all()
    if not rows:
        return []

    name_rows = (
        await session.execute(select(Group.id, Group.name).where(Group.id.in_(ancestor_ids)))
    ).all()
    name_by_id = {r.id: r.name for r in name_rows}

    # Higher rank = nearer to `group` (root is index 0 / lowest rank).
    nearest_rank = {gid: idx for idx, gid in enumerate(ancestor_ids)}

    best_by_user: dict[uuid.UUID, tuple[Membership, User]] = {}
    for membership, user in rows:
        if user.id in direct_user_ids:
            continue
        current = best_by_user.get(user.id)
        if current is None or nearest_rank[membership.group_id] > nearest_rank[current[0].group_id]:
            best_by_user[user.id] = (membership, user)

    entries = [
        GroupInheritedMemberEntry(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=membership.role,
            is_service_account=bool(user.is_service_account),
            source_group_id=membership.group_id,
            source_group_name=name_by_id.get(membership.group_id, ""),
        )
        for membership, user in best_by_user.values()
    ]
    return sorted(entries, key=lambda e: e.email)


async def get_group_members(
    session: AsyncSession, *, actor: CurrentUser, group_id: uuid.UUID
) -> GroupMembersResponse:
    group = await _load_accessible_group(session, actor=actor, group_id=group_id)

    direct_rows = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.group_id == group_id)
            .order_by(Membership.role.asc(), User.email.asc())
        )
    ).all()
    direct = [
        GroupMemberEntry(
            user_id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=m.role,
            is_service_account=bool(u.is_service_account),
        )
        for m, u in direct_rows
    ]
    direct_user_ids = {u.id for _, u in direct_rows}

    inherited = await _inherited_members(session, group=group, direct_user_ids=direct_user_ids)

    return GroupMembersResponse(direct=direct, inherited=inherited)


__all__ = [
    "GroupError",
    "GroupNotFound",
    "get_group_detail",
    "get_group_members",
    "list_groups",
]
