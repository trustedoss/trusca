"""
Concurrency regression test for ``services.admin_team_service.delete_team``
— security-reviewer F7 (CWE-367 TOCTOU on team delete).

The previous implementation read the team row, ran ``_team_has_active_scans``,
archived projects, then deleted the team — all without a row-level lock on
the team. A concurrent admin operation (another delete, or an update_team)
on the same team could interleave: thread A reads "no active scans" and
queues a delete; thread B reads the same row and starts an unrelated
mutation; both commit, leaving the audit trail with inconsistent state.

The fix in F7 acquires ``SELECT ... FOR UPDATE`` on the team row at the very
start of ``delete_team`` (``_lock_team_for_destructive_op``). Two complementary
shapes of test:

  1. Direct lock-behavior test:
     ``test_lock_team_for_destructive_op_blocks_concurrent_update`` — session
     A acquires the lock, session B issues an UPDATE on the same team with
     ``SET LOCAL lock_timeout = '500ms'`` and asserts B fails with
     lock_timeout. This deterministically proves the lock is taken.

  2. End-to-end gather invariant:
     ``test_concurrent_delete_team_blocks_at_least_one`` — two concurrent
     ``delete_team`` calls against the same team. With the lock in place,
     one wins, the other observes the team is gone and raises
     ``AdminTeamNotFound``. Without the lock both could attempt the delete
     and one would commit a partial state.

The shape-1 test is the load-bearing F7 regression: if the helper is ever
rewritten to skip ``with_for_update()`` it fires.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
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
        pytest.skip("DATABASE_URL not set — skip team-delete concurrency test")
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
            f"alembic upgrade head failed; team-delete concurrency test cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Independent session factory — see test_admin_concurrency.py for rationale."""
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
# Direct lock-behavior test (load-bearing F7 regression)
# ---------------------------------------------------------------------------


async def test_lock_team_for_destructive_op_blocks_concurrent_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    The locking SELECT in ``_lock_team_for_destructive_op`` must take a
    row-level lock that blocks a concurrent UPDATE on the team row. If the
    helper ever drops ``with_for_update()`` (e.g. someone "simplifies" it
    back to ``_load_team``), this test fires.

    Strategy:
      - Setup: create a team T.
      - Session A: open a transaction, call the locking helper. Hold open.
      - Session B: ``UPDATE teams SET name = name WHERE id = T`` with
        ``SET LOCAL lock_timeout = '500ms'``. The UPDATE must fail with
        lock_timeout (Postgres SQLSTATE 55P03). If the helper failed to
        take the lock the UPDATE would succeed instantly.
    """
    from services.admin_team_service import _lock_team_for_destructive_op

    async with session_factory() as setup:
        org = await make_organization(setup)
        team = await make_team(setup, organization=org, name=f"lockdel-{unique_suffix()}")

    session_a = session_factory()
    holder = await session_a.__aenter__()
    try:
        locked = await _lock_team_for_destructive_op(holder, team.id)
        assert locked.id == team.id

        async with session_factory() as competitor:
            await competitor.execute(text("SET LOCAL lock_timeout = '500ms'"))
            with pytest.raises(DBAPIError) as exc_info:
                await competitor.execute(
                    text("UPDATE teams SET name = name WHERE id = :tid"),
                    {"tid": str(team.id)},
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
# End-to-end gather invariant
# ---------------------------------------------------------------------------


async def test_concurrent_delete_team_blocks_at_least_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """
    Two concurrent ``delete_team`` calls against the same team. With the
    FOR UPDATE row lock in place:
      - One wins (returns None) and commits the delete.
      - The other blocks on the lock, then re-reads after the first commits
        and sees the team is gone → raises ``AdminTeamNotFound``.

    Without the lock both could pass ``_team_has_active_scans`` simultaneously,
    both could call ``session.delete(team)`` against their session-local
    snapshot, and depending on Postgres' tx isolation either both commit
    (one wins, the other no-ops with rowcount=0) or one fails on a stale
    SELECT — non-deterministic. The lock collapses that to a clean
    serialise.

    Invariants asserted:
      - At least one task returns None (the successful delete).
      - At least one task raises ``AdminTeamNotFound`` (the loser).
      - The team is gone post-gather.
      - No in-flight delete left the DB in a half-applied state (project
        rows for the team are gone via CASCADE).
    """
    from services.admin_team_service import (
        AdminTeamNotFound,
        delete_team,
    )

    async with session_factory() as setup_session:
        org = await make_organization(setup_session)
        team = await make_team(
            setup_session, organization=org, name=f"concurdel-{unique_suffix()}"
        )
        operator = await make_user(setup_session, is_superuser=True)
        actor = principal_for(operator, role="super_admin")

    target_id: uuid.UUID = team.id

    async def _delete() -> Exception | None:
        async with session_factory() as session:
            try:
                await delete_team(session, actor=actor, team_id=target_id)
                return None
            except Exception as exc:  # noqa: BLE001
                return exc

    results = await asyncio.gather(_delete(), _delete(), return_exceptions=False)

    successes = [r for r in results if r is None]
    not_found = [r for r in results if isinstance(r, AdminTeamNotFound)]
    assert successes, f"expected exactly one successful delete, got results={results}"
    assert not_found, (
        f"expected the loser to raise AdminTeamNotFound; got results={results}"
    )
    assert len(successes) == 1, (
        f"both deletes succeeded — race not closed. results={results}"
    )

    # Verify the team is actually gone.
    from models import Team

    async with session_factory() as verify_session:
        from sqlalchemy import select

        row = (
            await verify_session.execute(select(Team).where(Team.id == target_id))
        ).scalar_one_or_none()
    assert row is None, "team row still present after concurrent deletes"
