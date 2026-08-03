"""
Service-layer tests for ``services.admin_user_service`` — Phase 4 PR #13.

Drives the service against a live Postgres (DATABASE_URL) so the SQLAlchemy
listener fires and the audit_logs table records each mutation. Mirrors the
shape of ``tests/unit/test_project_service.py``.

Coverage:
  - happy path: list / get / role change / activate / deactivate / password reset
  - safety: last super_admin protection, self-modify protection
  - password reset: bcrypt-hashed (plaintext absent), TTL ~1h, supersedes prior
  - audit log row written for every mutation (incl. revocations + invalidations)
  - adversarial input parametrize on role / search (rejected at the boundary)
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
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
        pytest.skip("DATABASE_URL not set — skip admin_user_service tests")
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
            f"alembic upgrade head failed; admin_user_service tests cannot run\n"
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
# list_users
# ---------------------------------------------------------------------------


async def test_list_users_returns_pagination_envelope(db_session: AsyncSession) -> None:
    from services.admin_user_service import list_users

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    page = await list_users(db_session, actor=actor, page=1, page_size=10)
    assert page.page == 1
    assert page.page_size == 10
    assert page.total >= 1
    assert any(item.id == admin.id for item in page.items)


async def test_list_users_filter_by_active(db_session: AsyncSession) -> None:
    from services.admin_user_service import list_users

    admin = await make_user(db_session, is_superuser=True)
    inactive = await make_user(db_session, is_active=False)
    actor = principal_for(admin, role="super_admin")

    page = await list_users(db_session, actor=actor, active=False, page_size=200)
    ids = {item.id for item in page.items}
    assert inactive.id in ids


async def test_list_users_search_substring_email(db_session: AsyncSession) -> None:
    from services.admin_user_service import list_users

    admin = await make_user(db_session, is_superuser=True)
    target_email = f"hit-{unique_suffix()}@example.com"
    target = await make_user(db_session, email=target_email)
    actor = principal_for(admin, role="super_admin")

    page = await list_users(db_session, actor=actor, search="hit-", page_size=200)
    ids = {item.id for item in page.items}
    assert target.id in ids


async def test_list_users_rolls_up_effective_role_and_team_count(
    db_session: AsyncSession,
) -> None:
    """H-2: the list payload carries the membership rollup so the role column
    shows team_admins without opening the detail drawer."""
    from services.admin_user_service import list_users

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)

    team_admin = await make_user(db_session)
    await make_membership(db_session, user=team_admin, team=team_a, role="team_admin")
    await make_membership(db_session, user=team_admin, team=team_b, role="developer")

    developer = await make_user(db_session)
    await make_membership(db_session, user=developer, team=team_a, role="developer")

    orphan = await make_user(db_session)  # no memberships at all

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    page = await list_users(db_session, actor=actor, page_size=200)
    by_id = {item.id: item for item in page.items}

    assert by_id[team_admin.id].role == "team_admin"
    assert by_id[team_admin.id].team_count == 2
    assert by_id[developer.id].role == "developer"
    assert by_id[developer.id].team_count == 1
    assert by_id[orphan.id].role == "developer"
    assert by_id[orphan.id].team_count == 0
    assert by_id[admin.id].role == "super_admin"


# ---------------------------------------------------------------------------
# get_user_detail
# ---------------------------------------------------------------------------


async def test_get_user_detail_includes_memberships_and_scan_count(
    db_session: AsyncSession,
) -> None:
    from services.admin_user_service import get_user_detail

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await get_user_detail(db_session, actor=actor, user_id=user.id)
    assert detail.id == user.id
    assert detail.scan_count == 0
    assert len(detail.memberships) == 1
    assert detail.memberships[0].team_id == team.id
    assert detail.memberships[0].role == "developer"


async def test_get_user_detail_unknown_user_raises_not_found(
    db_session: AsyncSession,
) -> None:
    import uuid as _uuid

    from services.admin_user_service import AdminUserNotFound, get_user_detail

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(AdminUserNotFound):
        await get_user_detail(db_session, actor=actor, user_id=_uuid.uuid4())


# ---------------------------------------------------------------------------
# update_user_role — happy paths
# ---------------------------------------------------------------------------


async def test_update_user_role_to_team_admin_creates_membership(
    db_session: AsyncSession,
) -> None:
    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import update_user_role

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await update_user_role(
        db_session,
        actor=actor,
        user_id=user.id,
        payload=AdminUserRoleUpdate(role="team_admin", team_id=team.id),
    )
    roles = {m.team_id: m.role for m in detail.memberships}
    assert roles == {team.id: "team_admin"}


async def test_update_user_role_to_super_admin_sets_is_superuser(
    db_session: AsyncSession,
) -> None:
    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import update_user_role

    user = await make_user(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await update_user_role(
        db_session,
        actor=actor,
        user_id=user.id,
        payload=AdminUserRoleUpdate(role="super_admin"),
    )
    assert detail.is_superuser is True


async def test_update_user_role_team_role_without_team_id_raises(
    db_session: AsyncSession,
) -> None:
    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import InvalidRoleAssignment, update_user_role

    user = await make_user(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(InvalidRoleAssignment):
        await update_user_role(
            db_session,
            actor=actor,
            user_id=user.id,
            payload=AdminUserRoleUpdate(role="team_admin", team_id=None),
        )


# ---------------------------------------------------------------------------
# F9 — team-not-found preflight + IntegrityError → 422
# ---------------------------------------------------------------------------
# Previously a missing ``team_id`` (UUID for a deleted / never-existed team)
# bubbled up from the FK violation as a 500. Service now raises ``TeamNotFound``
# (status_code=422) with extension ``team_id`` so the router emits a proper
# RFC 7807 Problem Details. Two-layer guard: preflight ``SELECT id FROM teams``
# + IntegrityError catch on commit (delete-during-commit race).


async def test_update_user_role_unknown_team_id_raises_team_not_found(
    db_session: AsyncSession,
) -> None:
    """
    The preflight catches a stale UUID (the common case): admin opens the
    drawer, the team gets deleted in another tab, the admin clicks save.
    """
    import uuid

    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import TeamNotFound, update_user_role

    user = await make_user(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    bogus_team_id = uuid.uuid4()
    with pytest.raises(TeamNotFound) as exc_info:
        await update_user_role(
            db_session,
            actor=actor,
            user_id=user.id,
            payload=AdminUserRoleUpdate(role="team_admin", team_id=bogus_team_id),
        )

    # 422 + extension snake_case ``team_id`` (echoed verbatim into Problem
    # Details by the router).
    assert exc_info.value.status_code == 422
    assert exc_info.value.title == "Team Not Found"
    assert exc_info.value.extensions == {"team_id": str(bogus_team_id)}


async def test_update_user_role_unknown_team_id_does_not_500_or_leak_db_message(
    db_session: AsyncSession,
) -> None:
    """
    Defence-in-depth: the user-facing detail must be the controlled
    'team {uuid} does not exist' message — not the SQLAlchemy / Postgres
    FK violation text (which can include schema names, constraint names,
    etc.).
    """
    import uuid

    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import TeamNotFound, update_user_role

    user = await make_user(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    bogus_team_id = uuid.uuid4()
    with pytest.raises(TeamNotFound) as exc_info:
        await update_user_role(
            db_session,
            actor=actor,
            user_id=user.id,
            payload=AdminUserRoleUpdate(role="developer", team_id=bogus_team_id),
        )

    msg = str(exc_info.value)
    assert "does not exist" in msg
    # Drivers' raw FK violation messages typically contain "violates" or
    # "fk_". Neither should appear in our user-facing detail.
    assert "violates" not in msg.lower()
    assert "fk_" not in msg.lower()
    assert "constraint" not in msg.lower()


# Pure-unit pin tests for the F9 helpers (no DB) live in
# ``tests/unit/services/test_admin_user_team_not_found.py`` so they can run
# without the integration mark / migration fixture.


# ---------------------------------------------------------------------------
# update_user_role — safety
# ---------------------------------------------------------------------------


async def test_update_user_role_self_modify_blocked(db_session: AsyncSession) -> None:
    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import CannotModifySelf, update_user_role

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(CannotModifySelf):
        await update_user_role(
            db_session,
            actor=actor,
            user_id=admin.id,
            payload=AdminUserRoleUpdate(role="developer", team_id=team.id),
        )


async def test_update_user_role_demoting_last_super_admin_blocked(
    db_session: AsyncSession,
) -> None:
    """
    Set up a clean state where the *target* is the only active super_admin
    and confirm demotion is rejected.

    NOTE: the database is a shared scratch instance, so other tests may leave
    extra super_admins behind. We pre-deactivate every super_admin except
    our target so the count==1 invariant holds at the moment of the call.
    """
    from models import User
    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import (
        LastSuperAdminProtected,
        update_user_role,
    )

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    target = await make_user(db_session, is_superuser=True)
    actor_admin = await make_user(db_session, is_superuser=True)

    # Deactivate every super_admin EXCEPT our target so the protection rule
    # fires when we try to demote the target. Includes the actor — but the
    # actor's deactivation does NOT lock them out of this test (we still
    # have a valid CurrentUser principal in memory).
    others = (
        (
            await db_session.execute(
                select(User).where(
                    User.is_superuser.is_(True),
                    User.id.notin_([target.id]),
                )
            )
        )
        .scalars()
        .all()
    )
    for u in others:
        u.is_active = False
    await db_session.commit()

    actor = principal_for(actor_admin, role="super_admin")

    with pytest.raises(LastSuperAdminProtected):
        await update_user_role(
            db_session,
            actor=actor,
            user_id=target.id,
            payload=AdminUserRoleUpdate(role="developer", team_id=team.id),
        )


# ---------------------------------------------------------------------------
# deactivate_user
# ---------------------------------------------------------------------------


async def test_deactivate_user_revokes_refresh_tokens_and_marks_inactive(
    db_session: AsyncSession,
) -> None:
    from datetime import UTC, datetime, timedelta

    from models import RefreshToken
    from services.admin_user_service import deactivate_user

    user = await make_user(db_session)
    # Seed a couple of live refresh tokens.
    rt = RefreshToken(
        user_id=user.id,
        jti=f"j-{unique_suffix()}",
        token_hash=f"h-{unique_suffix()}",
        expires_at=datetime.now(tz=UTC) + timedelta(days=7),
    )
    db_session.add(rt)
    await db_session.commit()

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await deactivate_user(db_session, actor=actor, user_id=user.id)
    assert detail.is_active is False

    # Refresh token row is revoked.
    fresh = (
        await db_session.execute(select(RefreshToken).where(RefreshToken.id == rt.id))
    ).scalar_one()
    assert fresh.revoked_at is not None
    assert fresh.revoked_reason == "logout"


async def test_deactivate_user_self_modify_blocked(db_session: AsyncSession) -> None:
    from services.admin_user_service import CannotModifySelf, deactivate_user

    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    with pytest.raises(CannotModifySelf):
        await deactivate_user(db_session, actor=actor, user_id=admin.id)


async def test_deactivate_user_last_super_admin_blocked(
    db_session: AsyncSession,
) -> None:
    from models import User
    from services.admin_user_service import (
        LastSuperAdminProtected,
        deactivate_user,
    )

    target = await make_user(db_session, is_superuser=True)
    actor_admin = await make_user(db_session, is_superuser=True)

    # Deactivate every other super_admin so target is the only one left.
    others = (
        (
            await db_session.execute(
                select(User).where(
                    User.is_superuser.is_(True),
                    User.id.notin_([target.id]),
                )
            )
        )
        .scalars()
        .all()
    )
    for u in others:
        u.is_active = False
    await db_session.commit()

    actor = principal_for(actor_admin, role="super_admin")

    with pytest.raises(LastSuperAdminProtected):
        await deactivate_user(db_session, actor=actor, user_id=target.id)


async def test_activate_user_restores_active_flag(db_session: AsyncSession) -> None:
    from services.admin_user_service import activate_user

    user = await make_user(db_session, is_active=False)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    detail = await activate_user(db_session, actor=actor, user_id=user.id)
    assert detail.is_active is True


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------


async def test_initiate_password_reset_persists_bcrypt_hash_and_ttl(
    db_session: AsyncSession,
) -> None:
    from models import PasswordResetToken
    from services.admin_user_service import initiate_password_reset

    user = await make_user(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    token_id = await initiate_password_reset(db_session, actor=actor, user_id=user.id)

    row = (
        await db_session.execute(
            select(PasswordResetToken).where(PasswordResetToken.id == token_id)
        )
    ).scalar_one()
    # bcrypt hashes are ~60 chars, start with $2b$ or $2a$ or $2y$
    assert row.token_hash.startswith("$2")
    assert len(row.token_hash) >= 60
    assert row.user_id == user.id
    assert row.used_at is None
    assert row.invalidated_at is None

    # TTL ~ 1 hour
    delta = row.expires_at - datetime.now(tz=UTC)
    assert timedelta(minutes=55) <= delta <= timedelta(minutes=65)


async def test_initiate_password_reset_invalidates_prior_pending(
    db_session: AsyncSession,
) -> None:
    """Issuing a second reset for the same user marks the first as invalidated."""
    from models import PasswordResetToken
    from services.admin_user_service import initiate_password_reset

    user = await make_user(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    first_id = await initiate_password_reset(db_session, actor=actor, user_id=user.id)
    second_id = await initiate_password_reset(db_session, actor=actor, user_id=user.id)
    assert first_id != second_id

    first = (
        await db_session.execute(
            select(PasswordResetToken).where(PasswordResetToken.id == first_id)
        )
    ).scalar_one()
    second = (
        await db_session.execute(
            select(PasswordResetToken).where(PasswordResetToken.id == second_id)
        )
    ).scalar_one()
    assert first.invalidated_at is not None
    assert second.invalidated_at is None
    assert second.used_at is None


async def test_initiate_password_reset_writes_audit_with_masked_hash(
    db_session: AsyncSession,
) -> None:
    """The audit listener must mask the bcrypt hash to '***' in the diff."""
    from services.admin_user_service import initiate_password_reset

    user = await make_user(db_session)
    admin = await make_user(db_session, is_superuser=True)
    actor = principal_for(admin, role="super_admin")

    await initiate_password_reset(db_session, actor=actor, user_id=user.id)

    # The listener fires in before_flush, so the target_id is None for
    # inserts (the PK is server-generated by gen_random_uuid()). We match
    # by the user_id captured inside diff JSONB instead.
    rows = (
        await db_session.execute(
            text(
                "SELECT diff FROM audit_logs "
                "WHERE target_table = 'password_reset_tokens' "
                "  AND action = 'create' "
                "  AND diff @> CAST(:match AS jsonb)"
            ),
            {"match": f'{{"user_id": "{user.id}"}}'},
        )
    ).all()
    assert rows, "expected audit_logs row for password-reset token issuance"
    # Hash must be masked in every captured diff.
    for row in rows:
        diff = row.diff
        assert diff is not None
        assert diff.get("token_hash") == "***", diff


# ---------------------------------------------------------------------------
# Adversarial input — boundary validation by the schemas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_role",
    [
        "admin",
        "super",
        "",
        "SUPER_ADMIN",
        "developer ",  # trailing whitespace not normalized; rejected
        "team_admin\r\nDROP TABLE users",
        "javascript:alert(1)",
        "\x00root",
    ],
)
def test_admin_user_role_update_rejects_unknown_role(bad_role: str) -> None:
    """Pydantic v2 schema must reject every non-enum role string."""
    from pydantic import ValidationError

    from schemas.admin import AdminUserRoleUpdate

    with pytest.raises(ValidationError):
        AdminUserRoleUpdate.model_validate({"role": bad_role})


@pytest.mark.parametrize(
    "bad_role",
    [
        123,
        [],
        {"role": "super_admin"},
        None,
    ],
)
def test_admin_user_role_update_rejects_non_string_role(bad_role: object) -> None:
    from pydantic import ValidationError

    from schemas.admin import AdminUserRoleUpdate

    with pytest.raises(ValidationError):
        AdminUserRoleUpdate.model_validate({"role": bad_role})
