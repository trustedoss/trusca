"""
Admin team-management service — Phase 4 PR #13.

Pure DB I/O for the ``/v1/admin/teams/*`` HTTP surface.

Safety contracts (CLAUDE.md "조직/팀/권한 모델" + PR #13 spec §4.3):
  - Team deletion is rejected when any project owned by the team has a scan
    in ``status IN ('queued', 'running')`` — 422 with extension
    ``team_has_active_scans = true``. The admin must wait for the scan to
    finish (or cancel it via the scan-queue admin) before deleting the
    team.
  - Removing a team_admin is rejected when that membership is the *only*
    team_admin AND there are other members (developers) still in the team.
    The team would otherwise become un-administrable. 422 with extension
    ``last_team_admin_protected = true``. An empty team or a team where
    the only members are admins (so removal leaves a different admin
    behind) is fine.

Project hand-off on team delete (spec §4.3):
  Projects belong to teams via FK with ``ondelete="CASCADE"`` — physically,
  projects vanish with the team. The spec says "projects are archived"; we
  honour that as best we can by stamping ``archived_at = now()`` on every
  non-archived project owned by the team in the same transaction. The
  audit listener captures the archive event for the audit trail before the
  CASCADE wipes the row at commit. (Future PR can replace CASCADE with
  reassignment to a "deleted_team" tombstone team if the requirement
  hardens — that is a schema change and out of scope here.)

Audit hand-off:
  Mutating ORM rows is enough — the SQLAlchemy ``before_flush`` listener
  in ``core.audit`` produces the audit_logs row automatically.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import and_, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import audit_context
from core.security import CurrentUser
from models import Membership, Organization, Project, Scan, Team, User
from schemas.admin import (
    AdminTeamCreate,
    AdminTeamDetail,
    AdminTeamListItem,
    AdminTeamListPage,
    AdminTeamMember,
    AdminTeamMemberAdd,
    AdminTeamUpdate,
)

log = structlog.get_logger("admin.team.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AdminTeamError(Exception):
    status_code: int = 400
    title: str = "Admin Team Error"
    extensions: dict[str, object] = {}


class AdminTeamNotFound(AdminTeamError):
    status_code = 404
    title = "Team Not Found"


class AdminTeamSlugConflict(AdminTeamError):
    status_code = 409
    title = "Team Slug Conflict"


class TeamHasActiveScans(AdminTeamError):
    status_code = 422
    title = "Team Has Active Scans"
    extensions = {"team_has_active_scans": True}


class LastTeamAdminProtected(AdminTeamError):
    status_code = 422
    title = "Last Team Admin Protected"
    extensions = {"last_team_admin_protected": True}


class AdminTeamMembershipNotFound(AdminTeamError):
    status_code = 404
    title = "Team Membership Not Found"


class AdminTeamUserNotFound(AdminTeamError):
    status_code = 404
    title = "User Not Found"


class NoOrganizationConfigured(AdminTeamError):
    status_code = 422
    title = "No Organization Configured"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _bind_audit_team(team_id: uuid.UUID) -> None:
    ctx = dict(audit_context.get() or {})
    ctx["team_id"] = str(team_id)
    audit_context.set(ctx)


async def _load_team(session: AsyncSession, team_id: uuid.UUID) -> Team:
    stmt = select(Team).where(Team.id == team_id)
    team = (await session.execute(stmt)).scalar_one_or_none()
    if team is None:
        raise AdminTeamNotFound(f"team {team_id} not found")
    return team


async def _pick_default_org(session: AsyncSession) -> Organization:
    """
    Pick the lone organization for new-team creation.

    Single-org assumption (CLAUDE.md "조직/팀/권한 모델"): "Organization
    (배포 단위, 1개)". If the deployment somehow has multiple orgs we deterministically
    pick the oldest by created_at — Phase 8 SaaS will revisit this when
    multi-org becomes a real shape.
    """
    stmt = select(Organization).order_by(Organization.created_at.asc()).limit(1)
    org = (await session.execute(stmt)).scalar_one_or_none()
    if org is None:
        raise NoOrganizationConfigured("no organization is configured for this deployment")
    return org


async def _team_has_active_scans(session: AsyncSession, team_id: uuid.UUID) -> bool:
    """True if any project in this team has a scan in queued/running."""
    stmt = select(
        exists().where(
            and_(
                Project.team_id == team_id,
                Scan.project_id == Project.id,
                Scan.status.in_(("queued", "running")),
            )
        )
    )
    return bool((await session.execute(stmt)).scalar())


async def _lock_and_count_team_admins(session: AsyncSession, team_id: uuid.UUID) -> int:
    """
    Acquire a row-level lock on the team's team_admin membership rows and return the count.

    Closes the TOCTOU race that a plain ``SELECT count()`` exposes when
    used as a SELECT-then-mutate guard: two concurrent ``remove_team_member``
    or ``add_team_member`` (demotion) calls against the last team_admin
    could each read ``admin_count == 1`` and ``member_count > 1`` plus
    pass the inverted guard before either committed, leaving the team
    with zero admins while developers remain (CWE-367 TOCTOU).

    The locking SELECT pulls every (team_id, role='team_admin')
    membership row with ``FOR UPDATE``. Postgres holds those locks until
    commit/rollback so a second concurrent call against any of the same
    rows blocks until the first finishes and observes the post-commit
    count.

    Lock order convention: when a single transaction needs both the
    super-admin lock (``admin_user_service._lock_and_count_active_super_admins``)
    AND a team-admin lock, the super-admin lock MUST be acquired first.
    The current admin services only ever take one or the other inside a
    given transaction, so the convention is forward-compatible rather
    than load-bearing today.

    Reference: feedback_optimistic_concurrency_pattern memory; pattern
    matches ``vulnerability_service.py:309``.
    """
    locked = (
        (
            await session.execute(
                select(Membership)
                .where(Membership.team_id == team_id, Membership.role == "team_admin")
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    return len(locked)


async def _count_team_members(session: AsyncSession, team_id: uuid.UUID) -> int:
    stmt = select(func.count()).select_from(Membership).where(Membership.team_id == team_id)
    return int((await session.execute(stmt)).scalar_one())


async def _count_team_projects(
    session: AsyncSession, team_id: uuid.UUID, *, include_archived: bool = False
) -> int:
    stmt = select(func.count()).select_from(Project).where(Project.team_id == team_id)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    return int((await session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


async def list_teams(
    session: AsyncSession,
    *,
    actor: CurrentUser,  # noqa: ARG001
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
) -> AdminTeamListPage:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    base = select(Team)
    count_base = select(func.count()).select_from(Team)

    if search:
        like = f"%{search.strip()}%"
        base = base.where(Team.name.ilike(like))
        count_base = count_base.where(Team.name.ilike(like))

    total = int((await session.execute(count_base)).scalar_one())
    rows_stmt = (
        base.order_by(Team.created_at.desc(), Team.id.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    teams = list((await session.execute(rows_stmt)).scalars().all())

    items: list[AdminTeamListItem] = []
    for team in teams:
        member_count = await _count_team_members(session, team.id)
        project_count = await _count_team_projects(session, team.id)
        items.append(
            AdminTeamListItem(
                id=team.id,
                name=team.name,
                slug=team.slug,
                description=team.description,
                member_count=member_count,
                project_count=project_count,
                created_at=team.created_at,
            )
        )

    return AdminTeamListPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# Get detail
# ---------------------------------------------------------------------------


async def get_team_detail(
    session: AsyncSession,
    *,
    actor: CurrentUser,  # noqa: ARG001
    team_id: uuid.UUID,
) -> AdminTeamDetail:
    team = await _load_team(session, team_id)

    # Members: join memberships against users for the embedded list.
    member_stmt = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.team_id == team_id)
        .order_by(Membership.role.asc(), User.email.asc())
    )
    rows = (await session.execute(member_stmt)).all()
    members = [
        AdminTeamMember(
            user_id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=m.role,
        )
        for m, u in rows
    ]
    project_count = await _count_team_projects(session, team_id)

    return AdminTeamDetail(
        id=team.id,
        name=team.name,
        slug=team.slug,
        description=team.description,
        project_count=project_count,
        members=members,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


async def create_team(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    payload: AdminTeamCreate,
) -> AdminTeamDetail:
    org = await _pick_default_org(session)
    team = Team(
        organization_id=org.id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
    )
    session.add(team)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AdminTeamSlugConflict(
            f"a team with slug {payload.slug!r} already exists in this organization"
        ) from exc

    await session.refresh(team)
    log.info(
        "admin.team.created",
        actor_id=str(actor.id),
        team_id=str(team.id),
        slug=team.slug,
    )
    return await get_team_detail(session, actor=actor, team_id=team.id)


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


async def update_team(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    team_id: uuid.UUID,
    payload: AdminTeamUpdate,
) -> AdminTeamDetail:
    team = await _load_team(session, team_id)
    _bind_audit_team(team.id)

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(team, field, value)
    team.updated_at = _now()

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AdminTeamSlugConflict("team update violated a uniqueness constraint") from exc

    log.info(
        "admin.team.updated",
        actor_id=str(actor.id),
        team_id=str(team.id),
        fields=list(updates.keys()),
    )
    return await get_team_detail(session, actor=actor, team_id=team.id)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def delete_team(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    team_id: uuid.UUID,
) -> None:
    """
    Delete a team after archiving its projects.

    Refuses (422) if any project in the team has a scan in queued/running.
    Otherwise: stamps ``archived_at = now()`` on every non-archived project,
    then deletes the team. The CASCADE FKs handle memberships + projects
    physical removal — the archive UPDATE is purely so the audit log
    captures the project state right before deletion.
    """
    team = await _load_team(session, team_id)
    _bind_audit_team(team.id)

    if await _team_has_active_scans(session, team_id):
        raise TeamHasActiveScans(
            f"team {team_id} owns projects with scans currently queued or running"
        )

    # Archive non-archived projects so the audit log records the archive
    # event (the CASCADE on commit will physically remove the rows; the
    # audit row is still produced inside before_flush).
    now = _now()
    stmt = select(Project).where(Project.team_id == team_id, Project.archived_at.is_(None))
    projects = list((await session.execute(stmt)).scalars().all())
    for project in projects:
        project.archived_at = now
        project.updated_at = now

    await session.delete(team)
    await session.commit()
    log.info(
        "admin.team.deleted",
        actor_id=str(actor.id),
        team_id=str(team_id),
        archived_project_count=len(projects),
    )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


async def add_team_member(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    team_id: uuid.UUID,
    payload: AdminTeamMemberAdd,
) -> AdminTeamDetail:
    team = await _load_team(session, team_id)
    _bind_audit_team(team.id)

    user = (
        await session.execute(select(User).where(User.id == payload.user_id))
    ).scalar_one_or_none()
    if user is None:
        raise AdminTeamUserNotFound(f"user {payload.user_id} not found")

    existing = (
        await session.execute(
            select(Membership).where(
                Membership.team_id == team_id,
                Membership.user_id == payload.user_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        session.add(
            Membership(
                user_id=payload.user_id,
                team_id=team_id,
                role=payload.role,
            )
        )
    elif existing.role != payload.role:
        # Demoting a team_admin? Block if they are the last admin and other
        # non-admin members exist. Lock the team_admin membership rows
        # BEFORE the count check so two concurrent demotions cannot both
        # pass the ``admin_count > 1`` guard (CWE-367 TOCTOU; see
        # _lock_and_count_team_admins).
        if existing.role == "team_admin" and payload.role != "team_admin":
            admin_count = await _lock_and_count_team_admins(session, team_id)
            member_count = await _count_team_members(session, team_id)
            others = member_count - 1  # subtract this user
            if admin_count <= 1 and others > 0:
                raise LastTeamAdminProtected(
                    "cannot demote the last team_admin while other members exist"
                )
        existing.role = payload.role
        existing.updated_at = _now()

    await session.commit()
    log.info(
        "admin.team.member_added",
        actor_id=str(actor.id),
        team_id=str(team_id),
        target_user_id=str(payload.user_id),
        role=payload.role,
    )
    return await get_team_detail(session, actor=actor, team_id=team_id)


async def remove_team_member(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AdminTeamDetail:
    team = await _load_team(session, team_id)
    _bind_audit_team(team.id)

    membership = (
        await session.execute(
            select(Membership).where(
                Membership.team_id == team_id,
                Membership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AdminTeamMembershipNotFound(f"user {user_id} is not a member of team {team_id}")

    if membership.role == "team_admin":
        # Lock the team_admin membership row set inside this transaction
        # before the count check so two concurrent removals cannot both
        # pass the ``admin_count > 1`` guard (CWE-367 TOCTOU fix).
        admin_count = await _lock_and_count_team_admins(session, team_id)
        member_count = await _count_team_members(session, team_id)
        # If this is the only admin and there are other members, refuse —
        # the team would be unmanageable. If this is the only admin and
        # the team has just this one member (so removing leaves an empty
        # team), removal is fine.
        if admin_count <= 1 and member_count > 1:
            raise LastTeamAdminProtected(
                "cannot remove the last team_admin while other members exist"
            )

    await session.delete(membership)
    await session.commit()
    log.info(
        "admin.team.member_removed",
        actor_id=str(actor.id),
        team_id=str(team_id),
        target_user_id=str(user_id),
    )
    return await get_team_detail(session, actor=actor, team_id=team_id)


__all__ = [
    "AdminTeamError",
    "AdminTeamMembershipNotFound",
    "AdminTeamNotFound",
    "AdminTeamSlugConflict",
    "AdminTeamUserNotFound",
    "LastTeamAdminProtected",
    "NoOrganizationConfigured",
    "TeamHasActiveScans",
    "add_team_member",
    "create_team",
    "delete_team",
    "get_team_detail",
    "list_teams",
    "remove_team_member",
    "update_team",
]
