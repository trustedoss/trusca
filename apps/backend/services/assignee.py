# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Who may be named as the owner of a piece of work (ER28a).

Obligations have had this rule since they gained an assignee; findings need
the same one. It lives here rather than in either domain so there is one copy
to be right, instead of two to keep equal.

Each domain still raises its own error: this answers the question, it does not
decide what a caller does with the answer.

Phase 2 PR 2-C: this module's docstrings warned (before this PR) that
``GET /v1/projects/{project_id}/assignable-members`` derives its team from a
project and assumes "reaching a project == being on its team" — a premise
``core.authz.team_scope_filter``'s docstring pointed back here about, because
turning the group-hierarchy cascade on breaks it: a person reading a project
only through an ANCESTOR group's membership (cascade ON) is not a *direct*
member of the project's own team, so a query keyed on ``Membership.team_id ==
project.team_id`` alone would silently exclude them from the picker even
though ``core.authz.can_access_group`` / ``team_scope_filter`` already let
them read the project's findings and obligations.

The fix widens the *scope* this module's shared predicate accepts, not the
number of copies of it: every function below still funnels through
:func:`assignable_members_select`, now parameterized on the set of group ids
whose direct members are eligible (the project's own group, plus — cascade
ON only — every ancestor in its ``path``), so ``is_assignable_to_team`` (the
WRITE-time check) and ``list_assignable_members`` (the picker) can never
drift apart again: a name the picker offers is, by construction, a name the
write will accept, in both flag states.

With :func:`core.config.group_cascade_enabled` OFF (the default — this PR
does not flip it), :func:`_cascade_scope_ids` returns ``[team_id]`` alone and
every query below is byte-for-byte the pre-PR-2-C predicate
(``Membership.team_id == team_id`` vs. ``Membership.team_id IN
([team_id])`` — the same set).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import group_cascade_enabled
from models import Membership, User
from models.auth import Group


async def _cascade_scope_ids(session: AsyncSession, team_id: uuid.UUID) -> list[uuid.UUID]:
    """``[team_id]`` (flat) or ``[team_id, *ancestors]`` (cascade ON).

    A direct member of an ancestor group can, once the cascade flag is on,
    already reach *team_id*'s projects for READ through
    ``core.authz.team_scope_filter`` / ``can_access_group`` — the union of
    subtrees rooted at their direct memberships includes *team_id*. This
    widens the assignable set to match: an ancestor's direct member is
    exactly as "on the team" as a direct member of *team_id* itself, by the
    same cascade logic, so they may also be named as an owner of its work.

    Descendants of *team_id* are deliberately NOT included — cascade access
    flows down from an ancestor's membership, never up from a descendant's;
    a member of a child team has no standing over the parent's work.

    A non-existent *team_id* resolves to ``[team_id]`` with no ancestors
    (the ``Group`` lookup returns nothing) — callers already 404 on an
    unknown team/project before reaching this, so this is defense-in-depth,
    not a path any real caller takes.
    """
    if not group_cascade_enabled():
        return [team_id]
    path = (
        await session.execute(select(Group.path).where(Group.id == team_id))
    ).scalar_one_or_none()
    return [team_id, *path] if path else [team_id]


def assignable_members_select(
    scope_ids: Sequence[uuid.UUID],
) -> Select[tuple[uuid.UUID, str | None]]:
    """Everyone who may be named as an owner of work owned by *scope_ids*.

    A statement rather than a bare predicate, and that is the point. The
    conditions live here once so the question "may this person be named?" and
    the question "who may be named?" cannot drift; ER65 added the second
    caller, and writing the conditions again there would have made a list
    that offers people the write refuses, or hides people it would accept.

    *scope_ids* is one or more group ids whose DIRECT members are eligible —
    normally the output of :func:`_cascade_scope_ids`, i.e. ``[team_id]``
    (cascade off) or ``[team_id, *ancestors]`` (cascade on). A caller passing
    a single-element list gets exactly the pre-cascade query shape.

    Returning the query carries the join with them. An earlier version
    exported the ``and_(...)`` on its own, which compiles without complaint
    when a caller forgets to join ``Membership``: the security review
    compiled ``select(User.id).where(assignable_predicate(team))`` and got
    ``FROM users, memberships``, a cross join that returns every active
    person in the deployment rather than the team's. No error, no warning,
    and every existing test still green, because the two call sites at the
    time happened to write the join by hand. A helper whose reason to exist
    is future reuse cannot rely on future callers remembering a rule its
    docstring states.

    ``.distinct()`` matters once *scope_ids* can hold more than one id: a
    person who is a direct member of both the project's own team and an
    ancestor must appear once, not once per matching membership row.

    Callers narrow this rather than rebuild it: see the two below.
    """
    return (
        select(User.id, User.full_name)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.is_active.is_(True),
            User.is_service_account.is_(False),
            Membership.team_id.in_(scope_ids),
        )
        .distinct()
    )


async def is_assignable_to_team(
    session: AsyncSession, user_id: uuid.UUID, team_id: uuid.UUID
) -> bool:
    """Whether this person could actually pick the work up.

    Three conditions, and each one exists because naming somebody who cannot
    act is worse than leaving the work unassigned. An unassigned row is
    visibly waiting for somebody; a row assigned to someone who cannot act
    looks owned while nobody has been asked.

    - a member of the team that owns the work (or, cascade ON, of an
      ancestor of it — see :func:`_cascade_scope_ids`),
    - active: a deactivated account cannot sign in to do it,
    - not a service account: an API key is not a person who can be asked.

    This is a WRITE-time check. Nothing re-runs it when the world changes
    afterwards, so an assignment survives the assignee being deactivated or
    leaving the team. That is deliberate (silently dropping an assignment
    hides the work), but it means a reader has to be told when the assignee
    can no longer act, rather than assuming a name means the work is moving.
    """
    scope_ids = await _cascade_scope_ids(session, team_id)
    member = (
        await session.execute(
            assignable_members_select(scope_ids).where(User.id == user_id).limit(1)
        )
    ).first()
    return member is not None


async def list_assignable_members(
    session: AsyncSession, team_id: uuid.UUID
) -> list[tuple[uuid.UUID, str | None]]:
    """Everyone :func:`is_assignable_to_team` would accept for this team.

    Returns ``(user_id, full_name)`` and nothing else. Not the email: the admin
    team view carries one because an administrator auditing who can reach a
    team needs it, and a picker does not. Not the role, which does not change
    who may be named. Not service accounts, because they are not assignable, so
    what comes back is exactly the set the write accepts rather than a
    superset the caller has to filter.

    ``full_name`` is nullable and optional at registration, so it can be None.
    Dropping those people would be the same defect from the other side: they
    are assignable, and a list that hides them makes the write reachable only
    by somebody who already knows the id. The caller renders them.

    Ordered by name so the list is stable between calls; the ones with no name
    sort last together rather than being scattered through it.
    """
    scope_ids = await _cascade_scope_ids(session, team_id)
    rows = (
        await session.execute(
            assignable_members_select(scope_ids).order_by(
                User.full_name.asc().nullslast(), User.id.asc()
            )
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


__all__ = [
    "assignable_members_select",
    "is_assignable_to_team",
    "list_assignable_members",
]
