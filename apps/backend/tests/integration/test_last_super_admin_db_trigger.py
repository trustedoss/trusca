"""
Integration tests for the A5 ``trg_last_super_admin`` DB trigger
(migration 0013).

The trigger is the depth-in-defence guarantee that even raw-SQL operators
or future endpoints that bypass ``admin_user_service`` cannot leave the
system with zero active super_admins. The application-level
``LastSuperAdminProtected`` guard tests live in
``test_admin_user_service.py``; this file covers what happens when that
guard is sidestepped (raw SQL through the same DB connection the runtime
uses).

Test scoping note:
  Every test in this suite isolates itself by creating a fresh target
  super_admin AND deactivating every other active super_admin within the
  same transaction. That makes "the target is the last one" a
  per-transaction property; the deactivations are rolled back at the end
  of each test so other tests in the run keep their super_admins.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip last-super-admin trigger tests")
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
            f"alembic upgrade head failed; trigger tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


# ---------------------------------------------------------------------------
# Trigger behaviour
# ---------------------------------------------------------------------------


async def test_trigger_blocks_raw_demote_of_last_active_super_admin(
    client: AsyncClient,
) -> None:
    """``UPDATE users SET is_superuser = FALSE`` on the last active
    super_admin raises an IntegrityError with SQLSTATE 23514."""
    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session, is_superuser=True, is_active=True)
        target_id = str(target.id)

    async with factory() as session:
        # Make `target` the sole active super_admin within this transaction.
        await session.execute(
            text(
                "UPDATE users SET is_active = FALSE "
                "WHERE is_superuser = TRUE AND is_active = TRUE AND id <> :tid"
            ),
            {"tid": target_id},
        )
        with pytest.raises(IntegrityError) as excinfo:
            await session.execute(
                text("UPDATE users SET is_superuser = FALSE WHERE id = :tid"),
                {"tid": target_id},
            )
            # Force the trigger to fire (BEFORE UPDATE evaluates synchronously,
            # but the asyncpg driver may buffer until the next round-trip).
            await session.flush()
        # The error class must be IntegrityError — the application code
        # branches on it for the LastSuperAdminProtected fallback.
        assert excinfo.value.orig is not None
        pgcode = getattr(excinfo.value.orig, "sqlstate", None) or getattr(
            excinfo.value.orig, "pgcode", None
        )
        assert pgcode == "23514"
        await session.rollback()

    # Sanity: target is still an active super_admin after the rollback.
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT is_superuser, is_active FROM users WHERE id = :tid"
                ),
                {"tid": target_id},
            )
        ).first()
        assert row is not None
        assert row.is_superuser is True
        assert row.is_active is True


async def test_trigger_blocks_raw_deactivate_of_last_active_super_admin(
    client: AsyncClient,
) -> None:
    """``UPDATE users SET is_active = FALSE`` on the last active
    super_admin is also rejected (deactivation is the soft-delete path)."""
    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session, is_superuser=True, is_active=True)
        target_id = str(target.id)

    async with factory() as session:
        await session.execute(
            text(
                "UPDATE users SET is_active = FALSE "
                "WHERE is_superuser = TRUE AND is_active = TRUE AND id <> :tid"
            ),
            {"tid": target_id},
        )
        with pytest.raises(IntegrityError):
            await session.execute(
                text("UPDATE users SET is_active = FALSE WHERE id = :tid"),
                {"tid": target_id},
            )
            await session.flush()
        await session.rollback()


async def test_trigger_blocks_raw_delete_of_last_active_super_admin(
    client: AsyncClient,
) -> None:
    """``DELETE FROM users`` on the last active super_admin is rejected."""
    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session, is_superuser=True, is_active=True)
        target_id = str(target.id)

    async with factory() as session:
        await session.execute(
            text(
                "UPDATE users SET is_active = FALSE "
                "WHERE is_superuser = TRUE AND is_active = TRUE AND id <> :tid"
            ),
            {"tid": target_id},
        )
        with pytest.raises(IntegrityError):
            await session.execute(
                text("DELETE FROM users WHERE id = :tid"),
                {"tid": target_id},
            )
            await session.flush()
        await session.rollback()


async def test_trigger_allows_demote_when_other_active_super_admin_exists(
    client: AsyncClient,
) -> None:
    """When another active super_admin exists, demote of one passes the gate."""
    factory = await _factory(client)
    async with factory() as session:
        keeper = await make_user(session, is_superuser=True, is_active=True)
        target = await make_user(session, is_superuser=True, is_active=True)
        keeper_id = str(keeper.id)
        target_id = str(target.id)

    async with factory() as session:
        # Deactivate every super_admin except keeper + target so this test
        # is independent of other tests' state.
        await session.execute(
            text(
                "UPDATE users SET is_active = FALSE "
                "WHERE is_superuser = TRUE AND is_active = TRUE "
                "  AND id NOT IN (:k, :t)"
            ),
            {"k": keeper_id, "t": target_id},
        )
        # Demote target — keeper still leaves count >= 1, so allowed.
        await session.execute(
            text("UPDATE users SET is_superuser = FALSE WHERE id = :tid"),
            {"tid": target_id},
        )
        await session.flush()
        await session.rollback()  # do not pollute the global super_admin set


async def test_trigger_allows_delete_of_non_super_admin_user(
    client: AsyncClient,
) -> None:
    """A regular user (or even an inactive super_admin) deletes freely."""
    factory = await _factory(client)
    async with factory() as session:
        plain = await make_user(session, is_superuser=False, is_active=True)
        plain_id = str(plain.id)

    async with factory() as session:
        await session.execute(
            text("DELETE FROM users WHERE id = :pid"),
            {"pid": plain_id},
        )
        await session.commit()

    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT id FROM users WHERE id = :pid"),
                {"pid": plain_id},
            )
        ).first()
        assert row is None


async def test_trigger_allows_unrelated_column_update_on_last_super_admin(
    client: AsyncClient,
) -> None:
    """Updating an unrelated column (e.g. full_name) on the last
    super_admin is NOT a demote/deactivate transition — must pass."""
    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session, is_superuser=True, is_active=True)
        target_id = str(target.id)

    async with factory() as session:
        await session.execute(
            text(
                "UPDATE users SET is_active = FALSE "
                "WHERE is_superuser = TRUE AND is_active = TRUE AND id <> :tid"
            ),
            {"tid": target_id},
        )
        await session.execute(
            text("UPDATE users SET full_name = :n WHERE id = :tid"),
            {"n": "Renamed Operator", "tid": target_id},
        )
        await session.flush()
        await session.rollback()


async def test_trigger_blocks_multi_row_raw_demote(
    client: AsyncClient,
) -> None:
    """A single statement that demotes the last two active super_admins
    must be blocked. plpgsql's ``CommandCounterIncrement`` makes row 2's
    trigger see row 1's already-applied mutation, so row 2 raises
    even though the trigger fires per-row.

    This pins the in-statement behaviour the migration relies on; if a
    future PostgreSQL upgrade or a function rewrite changed the
    snapshot semantics, both rows might pass and the count would drop
    to zero — this test catches that regression.
    """
    factory = await _factory(client)
    async with factory() as session:
        keeper = await make_user(session, is_superuser=True, is_active=True)
        target = await make_user(session, is_superuser=True, is_active=True)
        keeper_id = str(keeper.id)
        target_id = str(target.id)

    async with factory() as session:
        await session.execute(
            text(
                "UPDATE users SET is_active = FALSE "
                "WHERE is_superuser = TRUE AND is_active = TRUE "
                "  AND id NOT IN (:k, :t)"
            ),
            {"k": keeper_id, "t": target_id},
        )
        # Single statement that targets BOTH active super_admins. Either
        # row 1 or row 2 will be the second one processed — the trigger
        # for that row must observe the already-applied mutation and
        # raise, regardless of execution order.
        with pytest.raises(IntegrityError):
            await session.execute(
                text(
                    "UPDATE users SET is_superuser = FALSE "
                    "WHERE id IN (:k, :t)"
                ),
                {"k": keeper_id, "t": target_id},
            )
            await session.flush()
        await session.rollback()


async def test_trigger_raise_message_matches_service_layer_matcher(
    client: AsyncClient,
) -> None:
    """Pin the exact text the migration RAISEs so a future migration
    that touches the message fails this test instead of silently
    breaking the application-layer ``_is_last_super_admin_violation``
    matcher (which substring-matches on the RAISE message because
    plpgsql RAISE EXCEPTION cannot attach a constraint_name).

    Reviewer L1 finding: the matcher couples to the migration message
    text. This test is the cheap structural backstop — change the
    message and you have to change the matcher in the same PR or this
    test fails."""
    from services.admin_user_service import _is_last_super_admin_violation

    factory = await _factory(client)
    async with factory() as session:
        target = await make_user(session, is_superuser=True, is_active=True)
        target_id = str(target.id)

    async with factory() as session:
        await session.execute(
            text(
                "UPDATE users SET is_active = FALSE "
                "WHERE is_superuser = TRUE AND is_active = TRUE AND id <> :tid"
            ),
            {"tid": target_id},
        )
        with pytest.raises(IntegrityError) as excinfo:
            await session.execute(
                text("UPDATE users SET is_superuser = FALSE WHERE id = :tid"),
                {"tid": target_id},
            )
            await session.flush()

        # Pin both: (1) SQLSTATE, (2) the substring the matcher relies on.
        orig = excinfo.value.orig
        pgcode = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        assert pgcode == "23514"
        assert "last active super_admin" in str(orig).lower()
        # And confirm the matcher itself returns True for this exact
        # exception — closes the loop.
        assert _is_last_super_admin_violation(excinfo.value) is True

        await session.rollback()
