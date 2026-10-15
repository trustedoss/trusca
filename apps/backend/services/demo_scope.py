# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Read scope for the public read-only demo.

The public demo publishes its seeded accounts and their shared password, and
two of those accounts are super admins so that visitors can see the admin
screens. A super admin normally reads across the whole deployment, and the
demo host also carries accounts that are not part of the demo dataset (the
operator's own administrator, for one). The admin screens that list people
and audit history therefore narrow themselves to the demo organisation while
``DEMO_READ_ONLY`` is on.

"Belongs to the demo" is defined the same way ``scripts/reset_demo`` defines
it: a user with a membership in a group of the ``demo-org`` organisation, and
for audit rows, a row whose actor is such a user or whose group is in that
organisation.

Every helper here reads the flag at call time (CLAUDE.md rule 11) and returns
``None`` when the demo is off, so a normal deploy runs exactly the query it ran
before.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from core.config import demo_read_only
from models import AuditLog, Membership, Organization, Team, User

# Slug of the organisation ``scripts/seed_demo`` creates. The seed and the
# reset both import this constant, so the scope and the dataset cannot name
# different organisations.
DEMO_ORG_SLUG = "demo-org"


def demo_group_ids() -> Select[tuple[uuid.UUID]]:
    """Subquery: ids of every group in the demo organisation."""
    return (
        select(Team.id)
        .join(Organization, Organization.id == Team.organization_id)
        .where(Organization.slug == DEMO_ORG_SLUG)
    )


def demo_user_ids() -> Select[tuple[uuid.UUID]]:
    """Subquery: ids of every user with a membership in a demo group."""
    return select(Membership.user_id).where(Membership.group_id.in_(demo_group_ids()))


async def is_demo_group(session: AsyncSession, group_id: uuid.UUID) -> bool:
    """True iff *group_id* is a group of the demo organisation."""
    found = (
        await session.execute(
            select(Team.id)
            .join(Organization, Organization.id == Team.organization_id)
            .where(Team.id == group_id, Organization.slug == DEMO_ORG_SLUG)
        )
    ).scalar_one_or_none()
    return found is not None


def demo_org_predicate() -> ColumnElement[bool] | None:
    """``WHERE`` clause limiting an ``Organization`` query to the demo, or ``None``."""
    if not demo_read_only():
        return None
    return Organization.slug == DEMO_ORG_SLUG


def demo_user_predicate() -> ColumnElement[bool] | None:
    """``WHERE`` clause limiting a ``User`` query to demo users, or ``None``."""
    if not demo_read_only():
        return None
    return User.id.in_(demo_user_ids())


def demo_group_predicate(column: Any) -> ColumnElement[bool] | None:
    """``WHERE`` clause limiting *column* (a group id) to demo groups, or ``None``."""
    if not demo_read_only():
        return None
    predicate: ColumnElement[bool] = column.in_(demo_group_ids())
    return predicate


def demo_audit_predicate() -> ColumnElement[bool] | None:
    """``WHERE`` clause limiting ``AuditLog`` rows to the demo, or ``None``.

    A row is kept when its actor is a demo user or its group is a demo group.
    Rows with neither (system rows, or rows about accounts outside the demo)
    are dropped.
    """
    if not demo_read_only():
        return None
    return or_(
        AuditLog.actor_user_id.in_(demo_user_ids()),
        AuditLog.group_id.in_(demo_group_ids()),
    )


__all__ = [
    "DEMO_ORG_SLUG",
    "demo_audit_predicate",
    "demo_group_ids",
    "demo_group_predicate",
    "demo_org_predicate",
    "demo_user_ids",
    "demo_user_predicate",
    "is_demo_group",
]
