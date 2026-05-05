"""
Shared test helpers for Phase 2 PR #7.

Builds organizations, teams, users, memberships, projects, and scans directly
against the live Postgres so each test starts from a clean, well-formed graph.

Why factories instead of conftest fixtures? Two reasons:

1. The auth integration suite already proved the "ASGITransport + raw INSERTs
   for setup" pattern works without truncating tables — we extend it.
2. Each test wants its own unique team/project, so a session-scoped fixture is
   the wrong shape; a function-call factory matches the existing pattern in
   `test_auth_flow.py::_unique_email`.

All factories use unique slugs/emails so parallel test runs do not collide.
They commit eagerly so the resulting rows survive across separate sessions —
that is what we want when we drive both the service layer and the HTTP layer
in the same test.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser, hash_password
from models import Membership, Organization, Project, Scan, Team, User


def unique_suffix() -> str:
    """Short uuid hex suitable for embedding in slugs/emails."""
    return uuid.uuid4().hex[:10]


def strong_password() -> str:
    """Meets the 12-char minimum + bcrypt-friendly content."""
    return f"Sup3rSecret!{unique_suffix()}"


# ---------------------------------------------------------------------------
# Organization / Team / User / Membership
# ---------------------------------------------------------------------------


async def make_organization(
    session: AsyncSession,
    *,
    name: str | None = None,
    slug: str | None = None,
) -> Organization:
    suffix = unique_suffix()
    org = Organization(
        name=name or f"Org {suffix}",
        slug=slug or f"org-{suffix}",
    )
    session.add(org)
    await session.commit()
    await session.refresh(org)
    return org


async def make_team(
    session: AsyncSession,
    *,
    organization: Organization,
    name: str | None = None,
    slug: str | None = None,
) -> Team:
    suffix = unique_suffix()
    team = Team(
        organization_id=organization.id,
        name=name or f"Team {suffix}",
        slug=slug or f"team-{suffix}",
    )
    session.add(team)
    await session.commit()
    await session.refresh(team)
    return team


async def make_user(
    session: AsyncSession,
    *,
    email: str | None = None,
    full_name: str | None = None,
    is_active: bool = True,
    is_superuser: bool = False,
) -> User:
    suffix = unique_suffix()
    user = User(
        email=email or f"user-{suffix}@example.com",
        hashed_password=hash_password(strong_password()),
        full_name=full_name or f"User {suffix}",
        is_active=is_active,
        is_superuser=is_superuser,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def make_membership(
    session: AsyncSession,
    *,
    user: User,
    team: Team,
    role: str = "developer",
) -> Membership:
    membership = Membership(user_id=user.id, team_id=team.id, role=role)
    session.add(membership)
    await session.commit()
    await session.refresh(membership)
    return membership


# ---------------------------------------------------------------------------
# Project / Scan
# ---------------------------------------------------------------------------


async def make_project(
    session: AsyncSession,
    *,
    team: Team,
    created_by: User | None = None,
    name: str | None = None,
    slug: str | None = None,
    visibility: str = "team",
    archived: bool = False,
) -> Project:
    suffix = unique_suffix()
    project = Project(
        team_id=team.id,
        name=name or f"Project {suffix}",
        slug=slug or f"project-{suffix}",
        visibility=visibility,
        created_by_user_id=created_by.id if created_by else None,
    )
    session.add(project)
    await session.commit()
    await session.refresh(project)
    if archived:
        from datetime import UTC, datetime

        project.archived_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
    return project


async def make_scan(
    session: AsyncSession,
    *,
    project: Project,
    requested_by: User | None = None,
    kind: str = "source",
    status: str = "queued",
) -> Scan:
    scan = Scan(
        project_id=project.id,
        kind=kind,
        status=status,
        progress_percent=0,
        requested_by_user_id=requested_by.id if requested_by else None,
        scan_metadata={},
    )
    session.add(scan)
    await session.commit()
    await session.refresh(scan)
    return scan


# ---------------------------------------------------------------------------
# CurrentUser principals (without going through /auth)
# ---------------------------------------------------------------------------


def principal_for(
    user: User,
    *,
    team_ids: Sequence[uuid.UUID] = (),
    role: str = "developer",
    team_roles: dict[uuid.UUID, str] | None = None,
) -> CurrentUser:
    """
    Build a `CurrentUser` directly. Skips the full /auth/login dance — used
    by service-layer unit tests that don't need to verify the JWT path.

    `team_roles` defaults to `{team_id: role for team_id in team_ids}` to
    match the production `_load_current_user` shape: every team in
    `team_ids` gets the same `role` unless callers override. Tests that need
    split memberships (different role per team — H-1 regression coverage)
    pass an explicit `team_roles` dict.
    """
    resolved_role = "super_admin" if user.is_superuser else role
    if team_roles is None:
        team_roles = {tid: role for tid in team_ids}
    return CurrentUser(
        id=user.id,
        email=user.email,
        role=resolved_role,
        team_ids=list(team_ids),
        team_roles=dict(team_roles),
        is_active=bool(user.is_active),
        is_superuser=bool(user.is_superuser),
    )


async def principal_loaded_from_db(session: AsyncSession, *, user: User) -> CurrentUser:
    """
    Build a CurrentUser by reading the user's memberships from the DB —
    matching the shape `core.security._load_current_user` produces.
    """
    from sqlalchemy.orm import selectinload

    stmt = select(User).where(User.id == user.id).options(selectinload(User.memberships))
    result = await session.execute(stmt)
    fresh = result.scalar_one()
    memberships = list(fresh.memberships)
    team_ids = [m.team_id for m in memberships]
    team_roles = {m.team_id: m.role for m in memberships}

    role_priority = {"developer": 1, "team_admin": 2, "super_admin": 3}
    if fresh.is_superuser:
        role = "super_admin"
    elif memberships:
        role = max((m.role for m in memberships), key=lambda r: role_priority.get(r, 0))
    else:
        role = "developer"

    return CurrentUser(
        id=fresh.id,
        email=fresh.email,
        role=role,
        team_ids=team_ids,
        team_roles=team_roles,
        is_active=bool(fresh.is_active),
        is_superuser=bool(fresh.is_superuser),
    )
