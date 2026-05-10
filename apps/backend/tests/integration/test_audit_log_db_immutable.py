"""DB-level immutability for ``audit_logs`` (A1 / sys-bug-audit-1).

Migration ``0012_audit_logs_immutable_trigger`` attaches a BEFORE UPDATE OR
DELETE trigger that raises SQLSTATE 23000 on any mutation. These tests
exercise the trigger directly against a fresh ``audit_logs`` row to confirm:

  - INSERT works (the listener path stays functional).
  - UPDATE raises IntegrityError mentioning ``TG_OP=UPDATE``.
  - DELETE raises IntegrityError mentioning ``TG_OP=DELETE``.
  - Adversarial column subsets (only ``action``; only ``ip``; ``diff`` JSONB
    overwrite) all hit the trigger — there is no "edit one column" loophole.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip audit_logs immutability tests")
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
            "alembic upgrade head failed; audit_logs trigger tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def session():
    url = _require_database_url()
    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _insert_one(session) -> uuid.UUID:
    """Insert a fresh audit_logs row and return its id."""
    row_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO audit_logs "
            "(id, created_at, action, target_table) "
            "VALUES (:id, :ts, :action, :target_table)"
        ),
        {
            "id": row_id,
            "ts": datetime.now(UTC),
            "action": "test.create",
            "target_table": "audit_logs_immutable_test",
        },
    )
    await session.commit()
    return row_id


async def test_insert_succeeds_trigger_does_not_block_inserts(session) -> None:
    """Sanity check — the trigger MUST NOT block INSERT.

    The audit listener path emits exclusively INSERTs; if the trigger
    accidentally caught INSERTs, every privileged write would 500.
    """
    row_id = await _insert_one(session)
    found = (
        await session.execute(
            text("SELECT id FROM audit_logs WHERE id = :id"),
            {"id": row_id},
        )
    ).scalar_one()
    assert found == row_id


async def test_update_action_blocked_with_integrity_error(session) -> None:
    row_id = await _insert_one(session)

    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(
            text("UPDATE audit_logs SET action = 'tampered' WHERE id = :id"),
            {"id": row_id},
        )
        await session.commit()

    # Trigger raise carries TG_OP in the message; surfaces via psycopg /
    # asyncpg as the original DETAIL on the IntegrityError.
    assert "TG_OP=UPDATE" in str(excinfo.value)
    await session.rollback()


async def test_delete_blocked_with_integrity_error(session) -> None:
    row_id = await _insert_one(session)

    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(
            text("DELETE FROM audit_logs WHERE id = :id"),
            {"id": row_id},
        )
        await session.commit()

    assert "TG_OP=DELETE" in str(excinfo.value)
    await session.rollback()


@pytest.mark.parametrize(
    "column,value",
    [
        # action (whitelisted enum-ish string in app code; still blocked)
        ("action", "tampered"),
        # ip (operational data; was treated as low-risk in app guards)
        ("ip", "203.0.113.1"),
        # user_agent (free-text; an attacker might want to scrub it)
        ("user_agent", "scrubbed"),
        # request_id (correlation id; tampering hides incident traces)
        ("request_id", "00000000-0000-0000-0000-000000000000"),
        # target_id — re-points the audit row at a different object.
        ("target_id", "ghosted-target"),
        # target_table — re-points the audit row at a different table.
        ("target_table", "users"),
        # created_at — antedating ("this happened before the breach").
        ("created_at", "2020-01-01 00:00:00+00"),
    ],
)
async def test_update_any_content_column_blocked(session, column, value) -> None:
    """Adversarial: confirm there is no "single column" UPDATE loophole on
    the content surface (id / created_at / action / target_table /
    target_id / request_id / ip / user_agent / diff).

    actor_user_id and team_id are intentionally NOT in this list — the
    FK ``ON DELETE SET NULL`` cascade needs them mutable to NULL when a
    referenced User / Team is removed. Their dedicated tests below
    distinguish the legitimate cascade (NULL transition) from the
    tampering case (rotation between two non-NULL ids).
    """
    row_id = await _insert_one(session)

    with pytest.raises(IntegrityError):
        await session.execute(
            text(f"UPDATE audit_logs SET {column} = :val WHERE id = :id"),
            {"val": value, "id": row_id},
        )
        await session.commit()
    await session.rollback()


async def test_actor_user_id_rotate_to_other_id_blocked(session) -> None:
    """Tampering case: rotate actor_user_id from NULL / non-NULL to a
    different non-NULL id ("it wasn't me, it was the other admin"). The
    function gates on ``NEW.actor_user_id IS NOT NULL`` so any non-NULL
    NEW value that differs from OLD is refused.
    """
    row_id = await _insert_one(session)

    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(
            text("UPDATE audit_logs SET actor_user_id = :v WHERE id = :id"),
            {"v": "00000000-0000-0000-0000-000000000000", "id": row_id},
        )
        await session.commit()
    assert "actor_user_id pin" in str(excinfo.value)
    await session.rollback()


async def test_actor_user_id_set_null_via_cascade_allowed(session) -> None:
    """Legitimate FK cascade: ``ON DELETE SET NULL`` on ``users.id``
    propagates as ``UPDATE audit_logs SET actor_user_id = NULL`` for
    every prior row. The trigger MUST allow this — every legitimate
    User delete with prior audit history depends on it.

    The seeded row's ``actor_user_id`` is already NULL (default), so a
    NULL → NULL rewrite is a no-op as far as ``IS DISTINCT FROM`` goes
    and the function's gate accepts it. The post-condition is simply
    that the UPDATE returns without raising.
    """
    row_id = await _insert_one(session)
    await session.execute(
        text("UPDATE audit_logs SET actor_user_id = NULL WHERE id = :id"),
        {"id": row_id},
    )
    await session.commit()


async def test_team_id_rotate_to_other_id_blocked(session) -> None:
    """Tampering case: rotate team_id between two non-NULL ids."""
    row_id = await _insert_one(session)

    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(
            text("UPDATE audit_logs SET team_id = :v WHERE id = :id"),
            {"v": "00000000-0000-0000-0000-000000000000", "id": row_id},
        )
        await session.commit()
    assert "team_id pin" in str(excinfo.value)
    await session.rollback()


async def test_team_id_set_null_via_cascade_allowed(session) -> None:
    """Legitimate FK cascade for team_id (mirror of the actor_user_id case)."""
    row_id = await _insert_one(session)
    await session.execute(
        text("UPDATE audit_logs SET team_id = NULL WHERE id = :id"),
        {"id": row_id},
    )
    await session.commit()


async def test_diff_jsonb_overwrite_blocked(session) -> None:
    """JSONB column overwrites also hit the trigger (no UPDATE bypass)."""
    row_id = await _insert_one(session)

    with pytest.raises(IntegrityError):
        await session.execute(
            text("UPDATE audit_logs SET diff = :diff::jsonb WHERE id = :id"),
            {"diff": '{"new_state": "tampered"}', "id": row_id},
        )
        await session.commit()
    await session.rollback()


async def test_multi_column_update_blocked(session) -> None:
    """Compound ``SET a=…, b=…, c=…`` is also caught (regression guard)."""
    row_id = await _insert_one(session)

    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "UPDATE audit_logs "
                "SET action = :a, target_table = :t, request_id = :r "
                "WHERE id = :id"
            ),
            {"a": "x", "t": "y", "r": "z", "id": row_id},
        )
        await session.commit()
    await session.rollback()


async def test_update_no_rows_matched_does_not_raise(session) -> None:
    """A WHERE clause that matches zero rows must NOT raise.

    BEFORE-row triggers fire per affected row. A zero-row UPDATE is a
    legal no-op and the trigger must stay quiet (false-positive guard).
    """
    bogus_id = uuid.uuid4()
    # Should succeed silently — no row matches, no trigger fires.
    await session.execute(
        text("UPDATE audit_logs SET action = 'x' WHERE id = :id"),
        {"id": bogus_id},
    )
    await session.commit()


async def test_truncate_blocked_with_integrity_error(session) -> None:
    """A1 (security-reviewer M1): TRUNCATE bypasses BEFORE-row triggers.

    The companion ``audit_logs_immutable_truncate`` (BEFORE TRUNCATE,
    FOR EACH STATEMENT) closes that gap. Without it, a single
    ``TRUNCATE TABLE audit_logs;`` would silently wipe the audit
    trail despite the row trigger.
    """
    await _insert_one(session)

    with pytest.raises(IntegrityError) as excinfo:
        await session.execute(text("TRUNCATE TABLE audit_logs"))
        await session.commit()

    # The shared function format includes the TG_OP — TRUNCATE here.
    assert "TG_OP=TRUNCATE" in str(excinfo.value)
    await session.rollback()
