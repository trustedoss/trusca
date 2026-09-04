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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Membership, User


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
            select(User.id)
            .join(Membership, Membership.user_id == User.id)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                User.is_service_account.is_(False),
                Membership.team_id == team_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return member is not None


__all__ = ["is_assignable_to_team"]
