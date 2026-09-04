# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Who may be named as the owner of a piece of work (ER28a).

Obligations have had this rule since they gained an assignee; findings need
the same one. It lives here rather than in either domain so there is one copy
to be right, instead of two to keep equal.

Each domain still raises its own error: this answers the question, it does not
decide what a caller does with the answer.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Membership, User


def assignable_members_select(team_id: uuid.UUID) -> Select[tuple[uuid.UUID, str | None]]:
    """Everyone who may be named as an owner of this team's work, as a query.

    A statement rather than a bare predicate, and that is the point. The three
    conditions live here once so the question "may this person be named?" and
    the question "who may be named?" cannot drift; ER65 added the second caller,
    and writing the conditions again there would have made a list that offers
    people the write refuses, or hides people it would accept.

    Returning the query carries the join with them. An earlier version exported
    the ``and_(...)`` on its own, which compiles without complaint when a caller
    forgets to join ``Membership``: the security review compiled
    ``select(User.id).where(assignable_predicate(team))`` and got
    ``FROM users, memberships``, a cross join that returns every active person
    in the deployment rather than the team's. No error, no warning, and every
    existing test still green, because the two call sites at the time happened
    to write the join by hand. A helper whose reason to exist is future reuse
    cannot rely on future callers remembering a rule its docstring states.

    Callers narrow this rather than rebuild it: see the two below.
    """
    return (
        select(User.id, User.full_name)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.is_active.is_(True),
            User.is_service_account.is_(False),
            Membership.team_id == team_id,
        )
    )


async def is_assignable_to_team(
    session: AsyncSession, user_id: uuid.UUID, team_id: uuid.UUID
) -> bool:
    """Whether this person could actually pick the work up.

    Three conditions, and each one exists because naming somebody who cannot
    act is worse than leaving the work unassigned. An unassigned row is
    visibly waiting for somebody; a row assigned to someone who cannot act
    looks owned while nobody has been asked.

    - a member of the team that owns the work,
    - active: a deactivated account cannot sign in to do it,
    - not a service account: an API key is not a person who can be asked.

    This is a WRITE-time check. Nothing re-runs it when the world changes
    afterwards, so an assignment survives the assignee being deactivated or
    leaving the team. That is deliberate (silently dropping an assignment
    hides the work), but it means a reader has to be told when the assignee
    can no longer act, rather than assuming a name means the work is moving.
    """
    member = (
        await session.execute(
            assignable_members_select(team_id).where(User.id == user_id).limit(1)
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
    rows = (
        await session.execute(
            assignable_members_select(team_id).order_by(
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
