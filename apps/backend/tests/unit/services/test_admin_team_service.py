"""
Service-layer tests for ``services.admin_team_service`` — Phase 4 PR #13.

Covers:
  - happy paths: list / detail / create / update / delete / add member / remove member
  - safety: team_has_active_scans, last_team_admin_protected, slug conflict
  - delete archives projects before CASCADE
  - audit log row written for every mutation
  - adversarial input parametrize on team name / slug / description / member role
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin_team_service tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            f"alembic upgrade head failed; admin_team_service tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    from core.audit import install_audit_listeners

    install_audit_listeners(factory)

    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# list_teams / get_team_detail
# ---------------------------------------------------------------------------


async def test_list_teams_returns_pagination_envelope(db_session: AsyncSession) -> None:
    from services.admin_team_service import list_teams

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    page = await list_teams(db_session, actor=actor, page=1, page_size=200)
    ids = {item.id for item in page.items}
    assert team.id in ids


async def test_list_teams_search_filter(db_session: AsyncSession) -> None:
    from services.admin_team_service import list_teams

    org = await make_organization(db_session)
    target_name = f"AdminTestTeam-{unique_suffix()}"
    team = await make_team(db_session, organization=org, name=target_name)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    page = await list_teams(db_session, actor=actor, search="AdminTestTeam-", page_size=200)
    ids = {item.id for item in page.items}
    assert team.id in ids


async def test_get_team_detail_includes_members_and_project_count(
    db_session: AsyncSession,
) -> None:
    from services.admin_team_service import get_team_detail

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    await make_project(db_session, team=team)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await get_team_detail(db_session, actor=actor, team_id=team.id)
    assert detail.id == team.id
    assert detail.project_count == 1
    assert any(m.user_id == user.id and m.role == "developer" for m in detail.members)


async def test_get_team_detail_unknown_team_raises(db_session: AsyncSession) -> None:
    from services.admin_team_service import AdminTeamNotFound, get_team_detail

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(AdminTeamNotFound):
        await get_team_detail(db_session, actor=actor, team_id=uuid.uuid4())


# ---------------------------------------------------------------------------
# create_team / update_team
# ---------------------------------------------------------------------------


async def test_create_team_persists_and_writes_audit(db_session: AsyncSession) -> None:
    from schemas.admin import AdminTeamCreate
    from services.admin_team_service import create_team

    await make_organization(db_session)  # ensures at least one org

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    suffix = unique_suffix()
    detail = await create_team(
        db_session,
        actor=actor,
        payload=AdminTeamCreate(
            name=f"New Team {suffix}",
            slug=f"new-{suffix}",
            description="hello",
        ),
    )
    assert detail.slug == f"new-{suffix}"

    rows = (
        await db_session.execute(
            text(
                "SELECT action FROM audit_logs "
                "WHERE target_table = 'teams' "
                "  AND diff @> CAST(:match AS jsonb)"
            ),
            {"match": f'{{"slug": "{detail.slug}"}}'},
        )
    ).all()
    assert rows, "expected audit_logs row for team create"
    assert any(r.action == "create" for r in rows)


async def test_create_team_duplicate_slug_returns_conflict(
    db_session: AsyncSession,
) -> None:
    from schemas.admin import AdminTeamCreate
    from services.admin_team_service import AdminTeamSlugConflict, create_team

    await make_organization(db_session)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    slug = f"dup-{unique_suffix()}"
    await create_team(
        db_session,
        actor=actor,
        payload=AdminTeamCreate(name="A", slug=slug),
    )
    with pytest.raises(AdminTeamSlugConflict):
        await create_team(
            db_session,
            actor=actor,
            payload=AdminTeamCreate(name="B", slug=slug),
        )


async def test_update_team_changes_name(db_session: AsyncSession) -> None:
    from schemas.admin import AdminTeamUpdate
    from services.admin_team_service import update_team

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await update_team(
        db_session,
        actor=actor,
        team_id=team.id,
        payload=AdminTeamUpdate(name="Renamed"),
    )
    assert detail.name == "Renamed"


# ---------------------------------------------------------------------------
# delete_team
# ---------------------------------------------------------------------------


async def test_delete_team_archives_projects_and_removes_team(
    db_session: AsyncSession,
) -> None:
    from services.admin_team_service import delete_team

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    await delete_team(db_session, actor=actor, team_id=team.id)

    # Audit row for the project archive (UPDATE captured before CASCADE)
    rows = (
        await db_session.execute(
            text(
                "SELECT action, diff FROM audit_logs "
                "WHERE target_table = 'projects' "
                "  AND target_id = :pid"
            ),
            {"pid": str(project.id)},
        )
    ).all()
    archive_rows = [r for r in rows if r.action == "archive" and "archived_at" in (r.diff or {})]
    assert archive_rows, "expected audit_logs row capturing the archive (M-5 soft-delete verb)"

    # Audit row for the team delete itself.
    rows = (
        await db_session.execute(
            text(
                "SELECT action FROM audit_logs "
                "WHERE target_table = 'teams' "
                "  AND target_id = :tid"
            ),
            {"tid": str(team.id)},
        )
    ).all()
    assert any(r.action == "delete" for r in rows), "expected team delete audit row"


async def test_delete_team_with_active_scan_blocked(
    db_session: AsyncSession,
) -> None:
    from services.admin_team_service import TeamHasActiveScans, delete_team

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    await make_scan(db_session, project=project, status="running")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(TeamHasActiveScans):
        await delete_team(db_session, actor=actor, team_id=team.id)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


async def test_add_team_member_inserts_membership(db_session: AsyncSession) -> None:
    from schemas.admin import AdminTeamMemberAdd
    from services.admin_team_service import add_team_member

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await add_team_member(
        db_session,
        actor=actor,
        team_id=team.id,
        payload=AdminTeamMemberAdd(user_id=user.id, role="developer"),
    )
    member_ids = {m.user_id for m in detail.members}
    assert user.id in member_ids


async def test_add_team_member_demotion_blocked_when_last_admin(
    db_session: AsyncSession,
) -> None:
    """Demoting the only team_admin while developers remain is rejected."""
    from schemas.admin import AdminTeamMemberAdd
    from services.admin_team_service import (
        LastTeamAdminProtected,
        add_team_member,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin_member = await make_user(db_session)
    dev = await make_user(db_session)
    await make_membership(db_session, user=admin_member, team=team, role="team_admin")
    await make_membership(db_session, user=dev, team=team, role="developer")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(LastTeamAdminProtected):
        await add_team_member(
            db_session,
            actor=actor,
            team_id=team.id,
            payload=AdminTeamMemberAdd(user_id=admin_member.id, role="developer"),
        )


async def test_remove_team_member_succeeds(db_session: AsyncSession) -> None:
    from models import Membership
    from services.admin_team_service import remove_team_member

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    await remove_team_member(db_session, actor=actor, team_id=team.id, user_id=user.id)

    remaining = (
        await db_session.execute(
            select(Membership).where(
                Membership.team_id == team.id,
                Membership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    assert remaining is None


async def test_remove_team_member_last_admin_with_others_blocked(
    db_session: AsyncSession,
) -> None:
    from services.admin_team_service import (
        LastTeamAdminProtected,
        remove_team_member,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin_member = await make_user(db_session)
    dev = await make_user(db_session)
    await make_membership(db_session, user=admin_member, team=team, role="team_admin")
    await make_membership(db_session, user=dev, team=team, role="developer")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(LastTeamAdminProtected):
        await remove_team_member(
            db_session,
            actor=actor,
            team_id=team.id,
            user_id=admin_member.id,
        )


async def test_remove_team_member_last_admin_when_alone_is_allowed(
    db_session: AsyncSession,
) -> None:
    """Removing the only admin from a team that has no other members is fine
    — the resulting empty team is administrable by super_admin alone."""
    from services.admin_team_service import remove_team_member

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin_member = await make_user(db_session)
    await make_membership(db_session, user=admin_member, team=team, role="team_admin")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await remove_team_member(
        db_session,
        actor=actor,
        team_id=team.id,
        user_id=admin_member.id,
    )
    assert detail.members == []


async def test_remove_team_member_unknown_user_raises_not_found(
    db_session: AsyncSession,
) -> None:
    from services.admin_team_service import (
        AdminTeamMembershipNotFound,
        remove_team_member,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(AdminTeamMembershipNotFound):
        await remove_team_member(
            db_session,
            actor=actor,
            team_id=team.id,
            user_id=uuid.uuid4(),
        )


async def test_add_team_member_unknown_user_raises_not_found(
    db_session: AsyncSession,
) -> None:
    from schemas.admin import AdminTeamMemberAdd
    from services.admin_team_service import (
        AdminTeamUserNotFound,
        add_team_member,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(AdminTeamUserNotFound):
        await add_team_member(
            db_session,
            actor=actor,
            team_id=team.id,
            payload=AdminTeamMemberAdd(user_id=uuid.uuid4(), role="developer"),
        )


# ---------------------------------------------------------------------------
# Adversarial input — schema-level validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",
        "  ",
        "X" * 100,  # > 64 chars
        "-leadingdash",
        "with space",
        "slug\r\nembedded",
        "drop;table",
        "../etc/passwd",
        "\x00null",
        "javascript:alert(1)",
        "a‮b",  # unicode RTL override
    ],
)
def test_admin_team_create_rejects_invalid_slug(bad_slug: str) -> None:
    from pydantic import ValidationError

    from schemas.admin import AdminTeamCreate

    with pytest.raises(ValidationError):
        AdminTeamCreate.model_validate({"name": "Valid Name", "slug": bad_slug})


def test_admin_team_create_normalizes_uppercase_slug_to_lowercase() -> None:
    """Upper-case slug input is folded to lower-case (admin-friendly), not rejected."""
    from schemas.admin import AdminTeamCreate

    payload = AdminTeamCreate.model_validate({"name": "X", "slug": "Upper"})
    assert payload.slug == "upper"


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "   ",
        "\t\n",
        "X" * 256,  # exceed max_length=255
    ],
)
def test_admin_team_create_rejects_blank_or_oversized_name(bad_name: str) -> None:
    from pydantic import ValidationError

    from schemas.admin import AdminTeamCreate

    with pytest.raises(ValidationError):
        AdminTeamCreate.model_validate({"name": bad_name, "slug": "valid-slug"})


@pytest.mark.parametrize(
    "bad_role",
    [
        "super_admin",  # not allowed for team membership
        "admin",
        "",
        "DEVELOPER",
        "developer ",
        "team_admin\r\n",
        123,
        [],
        None,
    ],
)
def test_admin_team_member_add_rejects_bad_role(bad_role: object) -> None:
    from pydantic import ValidationError

    from schemas.admin import AdminTeamMemberAdd

    with pytest.raises(ValidationError):
        AdminTeamMemberAdd.model_validate({"user_id": str(uuid.uuid4()), "role": bad_role})


@pytest.mark.parametrize(
    "bad_user_id",
    [
        "not-a-uuid",
        "",
        12345,
        "00000000-0000-0000-0000",  # truncated
        "../../etc/passwd",
        "javascript:alert(1)",
    ],
)
def test_admin_team_member_add_rejects_bad_user_id(bad_user_id: object) -> None:
    from pydantic import ValidationError

    from schemas.admin import AdminTeamMemberAdd

    with pytest.raises(ValidationError):
        AdminTeamMemberAdd.model_validate({"user_id": bad_user_id, "role": "developer"})
