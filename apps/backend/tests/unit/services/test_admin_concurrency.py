"""
Concurrency regression tests for the admin services — security-reviewer F1.

Closes CWE-367 (TOCTOU) on the last-super-admin / last-team-admin guards in
``services.admin_user_service`` and ``services.admin_team_service``.

The previous SELECT-then-mutate pattern in ``update_user_role``,
``deactivate_user``, ``add_team_member`` (demotion path), and
``remove_team_member`` let two concurrent admin requests each pass the
``count > 1`` guard before either committed, dropping the active super_admin
count below the floor or removing the only remaining team_admin while
developers were still in the team.

The fix locks the relevant row-set with ``with_for_update()`` inside the same
transaction as the mutation. These tests cover the race in two complementary
shapes:

  1. A *direct lock-behavior* test (``test_*_for_update_blocks_concurrent_update``)
     that holds an open transaction inside a session A which has just run
     the locking SELECT, then from session B issues an UPDATE on one of the
     locked rows with ``SET LOCAL lock_timeout = '500ms'`` and asserts B
     fails with ``lock_timeout``. This deterministically proves the
     ``with_for_update()`` actually takes the row lock — independent of any
     application-level scheduling.

  2. End-to-end *gather* tests (``test_concurrent_*_blocks_at_least_one``)
     that run two service calls in ``asyncio.gather`` against TWO
     independent ``AsyncSession`` instances (one per concurrent task)
     and assert the invariants:
       - At least one returns ``LastSuperAdminProtected`` /
         ``LastTeamAdminProtected``.
       - The post-gather active-super-admin / team-admin count is ``>= 1``.

The shape-1 tests are the load-bearing race regression: they would fail if
the service ever stopped calling ``with_for_update()``. The shape-2 tests
exercise the full call path and prove the count never drops to zero even
under concurrent admin actions.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
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
        pytest.skip("DATABASE_URL not set — skip admin concurrency tests")
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
            f"alembic upgrade head failed; admin concurrency tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """
    Yield a session factory that mints independent ``AsyncSession`` instances.

    Concurrency tests need TWO sessions running in parallel against the same
    Postgres so the row locks actually contend. A single shared session would
    serialize at the SQLAlchemy level (each ``execute`` awaits the previous
    one) and would not exercise the lock manager.
    """
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    try:
        yield factory
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _count_active_super_admins(session: AsyncSession) -> int:
    from models import User

    rows = (
        (
            await session.execute(
                select(User).where(User.is_active.is_(True), User.is_superuser.is_(True))
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


async def _count_team_admin_memberships(
    session: AsyncSession, team_id: uuid.UUID
) -> int:
    from models import Membership

    rows = (
        (
            await session.execute(
                select(Membership).where(
                    Membership.team_id == team_id, Membership.role == "team_admin"
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Direct lock-behavior tests (load-bearing F1 regressions)
# ---------------------------------------------------------------------------


async def test_lock_and_count_active_super_admins_blocks_concurrent_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    The locking SELECT in ``_lock_and_count_active_super_admins`` must take
    a row-level lock that blocks a concurrent UPDATE on any of the locked
    rows. This is the contract the F1 fix relies on — if it ever regresses
    (e.g. the helper is rewritten to use ``select(func.count())`` without
    materializing the rows, which silently drops the lock), this test
    fires.

    Strategy:
      - Setup: a known active super_admin user U.
      - Session A: open a transaction, call the locking helper.
      - Session B: attempt ``UPDATE users SET ... WHERE id = U.id`` with a
        500 ms ``lock_timeout``. The UPDATE must fail with the lock-timeout
        error code. If the helper failed to take the lock the UPDATE would
        succeed instantly.
      - Cleanup: rollback both sessions.
    """
    from services.admin_user_service import _lock_and_count_active_super_admins

    async with session_factory() as setup:
        # Deactivate every existing active super_admin so the locked set is
        # exactly {target}.
        from models import User

        existing = (
            (
                await setup.execute(
                    select(User).where(
                        User.is_active.is_(True), User.is_superuser.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        for u in existing:
            u.is_active = False
        await setup.commit()
        target = await make_user(setup, is_superuser=True)

    # Open the lock-holding transaction. We will NOT commit until after the
    # UPDATE attempt, so the lock is held for the duration.
    session_a = session_factory()
    holder = await session_a.__aenter__()
    try:
        # Acquire the lock.
        count = await _lock_and_count_active_super_admins(holder)
        assert count == 1, (
            f"expected exactly 1 active super_admin after setup, got {count}"
        )

        # In a separate connection, attempt the UPDATE with a tight lock_timeout.
        async with session_factory() as competitor:
            await competitor.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(DBAPIError) as exc_info:
                await competitor.execute(
                    text(
                        "UPDATE users SET full_name = full_name "
                        "WHERE id = :uid"
                    ),
                    {"uid": str(target.id)},
                )
            # The orig wraps a Postgres lock_timeout error. Postgres SQLSTATE
            # for lock_timeout is 55P03; for completeness we also accept the
            # English message fragment.
            err = str(exc_info.value).lower()
            assert "lock" in err and ("timeout" in err or "55p03" in err), (
                f"expected lock_timeout error, got: {exc_info.value!r}"
            )
            await competitor.rollback()
    finally:
        await holder.rollback()
        await session_a.__aexit__(None, None, None)


async def test_lock_and_count_team_admins_blocks_concurrent_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Same lock-behavior test, but for the team_admin membership row set.
    The locking helper must block a concurrent UPDATE on any of the locked
    membership rows.
    """
    from services.admin_team_service import _lock_and_count_team_admins

    async with session_factory() as setup:
        org = await make_organization(setup)
        team = await make_team(
            setup, organization=org, name=f"lock-{unique_suffix()}"
        )
        admin = await make_user(setup)
        await make_membership(setup, user=admin, team=team, role="team_admin")

    session_a = session_factory()
    holder = await session_a.__aenter__()
    try:
        count = await _lock_and_count_team_admins(holder, team.id)
        assert count == 1, f"expected 1 team_admin after setup, got {count}"

        async with session_factory() as competitor:
            await competitor.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(DBAPIError) as exc_info:
                await competitor.execute(
                    text(
                        "UPDATE memberships SET role = role "
                        "WHERE team_id = :tid AND user_id = :uid"
                    ),
                    {"tid": str(team.id), "uid": str(admin.id)},
                )
            err = str(exc_info.value).lower()
            assert "lock" in err and ("timeout" in err or "55p03" in err), (
                f"expected lock_timeout error, got: {exc_info.value!r}"
            )
            await competitor.rollback()
    finally:
        await holder.rollback()
        await session_a.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# End-to-end gather invariants
# ---------------------------------------------------------------------------


async def test_concurrent_deactivate_last_super_admin_blocks_at_least_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Two concurrent ``deactivate_user`` calls against the last two super-admins
    must NOT both succeed. With the FOR UPDATE row lock in place, the first
    transaction holds the lock on the active-super-admin row set; the second
    blocks at the Postgres level until the first commits, then re-reads the
    count and (correctly) raises LastSuperAdminProtected.

    Invariants asserted:
      - At least one task returns LastSuperAdminProtected.
      - The post-gather active-super-admin count is >= 1.
    """
    from services.admin_user_service import (
        LastSuperAdminProtected,
        deactivate_user,
    )

    # Setup: two target super_admins (A, B) plus an "operator" super_admin C
    # used as the actor (the service forbids self-deactivate).
    async with session_factory() as setup_session:
        from models import User

        existing = (
            (
                await setup_session.execute(
                    select(User).where(
                        User.is_active.is_(True), User.is_superuser.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        for u in existing:
            u.is_active = False
        await setup_session.commit()

        target_a = await make_user(setup_session, is_superuser=True)
        target_b = await make_user(setup_session, is_superuser=True)
        operator = await make_user(setup_session, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

        # Deactivate operator so the active-super-admin set is exactly {A, B}.
        # With three rows present neither demote alone would trigger the
        # ``count <= 1`` guard, defeating the test.
        operator.is_active = False
        await setup_session.commit()

    async with session_factory() as check_session:
        active_count = await _count_active_super_admins(check_session)
        assert active_count == 2, (
            f"expected 2 active super_admins right before gather, got {active_count}"
        )

    async def _deactivate(target_id: uuid.UUID) -> Exception | None:
        async with session_factory() as session:
            try:
                await deactivate_user(session, actor=actor, user_id=target_id)
                return None
            except Exception as exc:  # noqa: BLE001 — capture either side
                return exc

    results = await asyncio.gather(
        _deactivate(target_a.id),
        _deactivate(target_b.id),
        return_exceptions=False,
    )

    raised = [r for r in results if isinstance(r, LastSuperAdminProtected)]
    assert raised, (
        f"expected at least one LastSuperAdminProtected; got results={results}"
    )

    async with session_factory() as verify_session:
        active_count = await _count_active_super_admins(verify_session)
    assert active_count >= 1, (
        f"active super_admin count dropped to {active_count}; race not closed"
    )


async def test_concurrent_demote_last_super_admin_blocks_at_least_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Two concurrent ``update_user_role`` demotions against the last two
    super-admins must NOT both succeed. Same race as the deactivate variant
    via the role-change path.
    """
    from schemas.admin import AdminUserRoleUpdate
    from services.admin_user_service import (
        LastSuperAdminProtected,
        update_user_role,
    )

    async with session_factory() as setup_session:
        from models import User

        existing = (
            (
                await setup_session.execute(
                    select(User).where(
                        User.is_active.is_(True), User.is_superuser.is_(True)
                    )
                )
            )
            .scalars()
            .all()
        )
        for u in existing:
            u.is_active = False
        await setup_session.commit()

        org = await make_organization(setup_session)
        team = await make_team(setup_session, organization=org)
        target_a = await make_user(setup_session, is_superuser=True)
        target_b = await make_user(setup_session, is_superuser=True)
        operator = await make_user(setup_session, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

        operator.is_active = False
        await setup_session.commit()

    async with session_factory() as check_session:
        active_count = await _count_active_super_admins(check_session)
        assert active_count == 2, (
            f"expected 2 active super_admins before gather, got {active_count}"
        )

    payload = AdminUserRoleUpdate(role="developer", team_id=team.id)

    async def _demote(target_id: uuid.UUID) -> Exception | None:
        async with session_factory() as session:
            try:
                await update_user_role(
                    session, actor=actor, user_id=target_id, payload=payload
                )
                return None
            except Exception as exc:  # noqa: BLE001
                return exc

    results = await asyncio.gather(
        _demote(target_a.id),
        _demote(target_b.id),
        return_exceptions=False,
    )

    raised = [r for r in results if isinstance(r, LastSuperAdminProtected)]
    assert raised, (
        f"expected at least one LastSuperAdminProtected on concurrent demote; "
        f"got results={results}"
    )

    async with session_factory() as verify_session:
        active_count = await _count_active_super_admins(verify_session)
    assert active_count >= 1, (
        f"active super_admin count dropped to {active_count}; race not closed"
    )


async def test_concurrent_remove_last_team_admin_blocks_at_least_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Two concurrent ``remove_team_member`` calls against the last two team
    admins (while developers remain in the team) must NOT both succeed.
    """
    from services.admin_team_service import (
        LastTeamAdminProtected,
        remove_team_member,
    )

    async with session_factory() as setup_session:
        org = await make_organization(setup_session)
        team = await make_team(
            setup_session, organization=org, name=f"concur-{unique_suffix()}"
        )
        admin_a = await make_user(setup_session)
        admin_b = await make_user(setup_session)
        developer = await make_user(setup_session)
        await make_membership(setup_session, user=admin_a, team=team, role="team_admin")
        await make_membership(setup_session, user=admin_b, team=team, role="team_admin")
        await make_membership(
            setup_session, user=developer, team=team, role="developer"
        )

        operator = await make_user(setup_session, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

        admin_count = await _count_team_admin_memberships(setup_session, team.id)
        assert admin_count == 2, (
            f"expected 2 team_admins before gather, got {admin_count}"
        )

    async def _remove(target_user_id: uuid.UUID) -> Exception | None:
        async with session_factory() as session:
            try:
                await remove_team_member(
                    session, actor=actor, team_id=team.id, user_id=target_user_id
                )
                return None
            except Exception as exc:  # noqa: BLE001
                return exc

    results = await asyncio.gather(
        _remove(admin_a.id),
        _remove(admin_b.id),
        return_exceptions=False,
    )

    raised = [r for r in results if isinstance(r, LastTeamAdminProtected)]
    assert raised, (
        f"expected at least one LastTeamAdminProtected; got results={results}"
    )

    async with session_factory() as verify_session:
        admin_count = await _count_team_admin_memberships(verify_session, team.id)
    assert admin_count >= 1, (
        f"team_admin count dropped to {admin_count}; race not closed"
    )


async def test_concurrent_demote_last_team_admin_blocks_at_least_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Two concurrent ``add_team_member`` demote-on-existing-membership calls
    against the last two team_admins must NOT both succeed.
    """
    from schemas.admin import AdminTeamMemberAdd
    from services.admin_team_service import (
        LastTeamAdminProtected,
        add_team_member,
    )

    async with session_factory() as setup_session:
        org = await make_organization(setup_session)
        team = await make_team(
            setup_session,
            organization=org,
            name=f"concur-demote-{unique_suffix()}",
        )
        admin_a = await make_user(setup_session)
        admin_b = await make_user(setup_session)
        developer = await make_user(setup_session)
        await make_membership(setup_session, user=admin_a, team=team, role="team_admin")
        await make_membership(setup_session, user=admin_b, team=team, role="team_admin")
        await make_membership(
            setup_session, user=developer, team=team, role="developer"
        )

        operator = await make_user(setup_session, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

        admin_count = await _count_team_admin_memberships(setup_session, team.id)
        assert admin_count == 2, (
            f"expected 2 team_admins before gather, got {admin_count}"
        )

    async def _demote(target_user_id: uuid.UUID) -> Exception | None:
        payload = AdminTeamMemberAdd(user_id=target_user_id, role="developer")
        async with session_factory() as session:
            try:
                await add_team_member(
                    session, actor=actor, team_id=team.id, payload=payload
                )
                return None
            except Exception as exc:  # noqa: BLE001
                return exc

    results = await asyncio.gather(
        _demote(admin_a.id),
        _demote(admin_b.id),
        return_exceptions=False,
    )

    raised = [r for r in results if isinstance(r, LastTeamAdminProtected)]
    assert raised, (
        f"expected at least one LastTeamAdminProtected on concurrent demote; "
        f"got results={results}"
    )

    async with session_factory() as verify_session:
        admin_count = await _count_team_admin_memberships(verify_session, team.id)
    assert admin_count >= 1, (
        f"team_admin count dropped to {admin_count}; race not closed"
    )
