# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The ER32 hole in the audit trigger stays the size it was cut (0080).

``audit_logs`` is append-only, and anonymising a user has to reach inside it
to clear the subject's ip and user_agent. Migration 0080 cuts one exception
for that. An exception in an immutability control is the kind of thing that
widens later without anyone deciding to widen it, so these tests pin its edges.

Two layers, tested separately, because otherwise only one of them is tested
---------------------------------------------------------------------------
The application role cannot perform the scrub for two independent reasons: it
holds no UPDATE grant on ``audit_logs`` at all, and the trigger's exception
requires the caller to be a member of the table's owner. In the deployed
configuration the missing grant fires first, so a test that only tries the
UPDATE as the app role proves the grant and says nothing about the trigger.
That matters because the grant is the layer likely to move: migration 0072
exists to adjust app-role DML grants, and the day one widens to include
``audit_logs`` the trigger becomes the only thing left. So the second test
grants UPDATE on purpose, confirms the trigger refuses anyway, and hands the
grant back.

Why the no-op test is here
--------------------------
The trigger compares values, so an UPDATE writing a column the value it
already holds changes nothing and is allowed. Point a forged scrub at a row
whose ip is already NULL and Postgres answers ``UPDATE 1``, which reads like
the exception being bypassed. It is not, and this test says so out loud, so
that nobody later "simplifies" the layer-two test onto an already-scrubbed row
and leaves behind an assertion that cannot fail.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import make_user

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "trustedoss_app"


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set, skipping audit scrub exception tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; audit scrub exception tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    url = _require_database_url()
    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _require_app_role(session: AsyncSession) -> None:
    exists = (
        await session.execute(
            text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
        )
    ).scalar_one_or_none()
    if exists is None:
        pytest.skip(f"{APP_ROLE} not provisioned in this database")


async def _audit_row(
    session: AsyncSession, *, actor_id: uuid.UUID, ip: str | None = "198.51.100.7"
) -> uuid.UUID:
    """One audit row with client details actually populated.

    ``ip`` is a parameter so the no-op test can ask for a row that has already
    been scrubbed, which is the state that makes the forged UPDATE meaningless.
    """
    row_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO audit_logs "
            "(id, created_at, actor_user_id, action, target_table, target_id, "
            " diff, ip, user_agent) "
            "VALUES (:id, :ts, :actor, 'update', 'users', :target, "
            " CAST(:diff AS jsonb), :ip, :ua)"
        ),
        {
            "id": row_id,
            "ts": datetime.now(UTC),
            "actor": actor_id,
            "target": str(actor_id),
            "diff": '{"email": {"old": "before@example.com"}}',
            "ip": ip,
            "ua": None if ip is None else "curl/8",
        },
    )
    await session.commit()
    return row_id


async def _client_details(
    session: AsyncSession, row_id: uuid.UUID
) -> tuple[str | None, str | None]:
    ip, user_agent = (
        await session.execute(
            text("SELECT ip::text, user_agent FROM audit_logs WHERE id = :id"),
            {"id": row_id},
        )
    ).one()
    return ip, user_agent


async def test_the_app_role_holds_no_update_grant_on_audit_logs(
    session: AsyncSession,
) -> None:
    """Layer one. Nothing the application can execute reaches an UPDATE here."""
    await _require_app_role(session)
    granted = (
        await session.execute(
            text(
                "SELECT 1 FROM information_schema.table_privileges "
                "WHERE grantee = :r AND table_name = 'audit_logs' "
                "AND privilege_type = 'UPDATE'"
            ),
            {"r": APP_ROLE},
        )
    ).scalar_one_or_none()
    assert granted is None, (
        f"{APP_ROLE} has gained UPDATE on audit_logs. That is not automatically "
        "wrong, but the trigger is now the only thing standing between a "
        "compromised application and its own audit trail; read the module "
        "docstring before accepting this"
    )


async def test_the_trigger_refuses_the_app_role_even_holding_the_grant(
    session: AsyncSession,
) -> None:
    """Layer two, tested on its own by removing layer one for the duration.

    This is the adversary the exception is shaped against: code running as the
    application that has reached raw SQL, knows the flag's name, and sets it
    itself. Custom GUCs are settable by any role, so the flag is not a
    credential; membership of the table owner is, and the app role does not
    have it.
    """
    await _require_app_role(session)
    actor = await make_user(session)
    await session.commit()
    row_id = await _audit_row(session, actor_id=actor.id)

    before = await _client_details(session, row_id)
    assert before[0] is not None and before[1] is not None, (
        "precondition failed: the row must carry client details, or the UPDATE "
        "below changes nothing and the test proves nothing"
    )

    await session.execute(text(f"GRANT UPDATE ON audit_logs TO {APP_ROLE}"))
    await session.commit()
    try:
        await session.execute(text(f"SET ROLE {APP_ROLE}"))
        await session.execute(text("SELECT set_config('trusca.audit_scrub','on',false)"))
        with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
            await session.execute(
                text(
                    "UPDATE audit_logs SET ip = NULL, user_agent = NULL "
                    "WHERE id = :id"
                ),
                {"id": row_id},
            )
            await session.commit()
        assert "append-only" in str(excinfo.value)
        await session.rollback()
    finally:
        await session.execute(text("RESET ROLE"))
        await session.execute(text(f"REVOKE UPDATE ON audit_logs FROM {APP_ROLE}"))
        await session.commit()

    after = await _client_details(session, row_id)
    assert after == before, "the client details did not survive the forged scrub"


async def test_a_no_op_update_passes_and_is_not_a_bypass(
    session: AsyncSession,
) -> None:
    """The trap that made an earlier reading of this exception wrong.

    Asserted rather than merely commented, because the comment is what somebody
    skips. If a future change makes the trigger reject no-op UPDATEs this goes
    red and the layer-two test above can be simplified; until then it records
    why that test insists on a row with live values.
    """
    await _require_app_role(session)
    actor = await make_user(session)
    await session.commit()
    row_id = await _audit_row(session, actor_id=actor.id, ip=None)

    result = await session.execute(
        text("UPDATE audit_logs SET ip = NULL, user_agent = NULL WHERE id = :id"),
        {"id": row_id},
    )
    matched = result.rowcount  # type: ignore[attr-defined]
    await session.commit()

    assert matched == 1, (
        "a no-op UPDATE on an already-scrubbed row no longer reports a "
        "matched row; the layer-two test's insistence on live values may now "
        "be unnecessary"
    )


async def test_scrub_refuses_until_a_second_person_has_approved(
    session: AsyncSession,
) -> None:
    """The function's own gate, walked through the three states that precede it.

    The database checks this itself rather than trusting the operator command
    to have checked: the command is what an operator runs by hand at the point
    where a mistake cannot be undone.
    """
    subject = await make_user(session)
    requester = await make_user(session, is_superuser=True)
    approver = await make_user(session, is_superuser=True)
    await session.commit()
    # Held as plain values: this test rolls back between states, and a
    # rollback expires ORM attributes, so a later ``subject.id`` would try to
    # reload from the database in the middle of an assertion.
    subject_id, requester_id, approver_id = subject.id, requester.id, approver.id
    row_id = await _audit_row(session, actor_id=subject_id)

    async def _scrub() -> int:
        value = (
            await session.execute(
                text("SELECT audit_logs_scrub_pii(:s)"), {"s": subject_id}
            )
        ).scalar_one()
        return int(value)

    with pytest.raises(DBAPIError) as no_request:
        await _scrub()
    assert "no approved anonymisation request" in str(no_request.value)
    await session.rollback()

    await session.execute(
        text(
            "INSERT INTO user_anonymisation_requests "
            "(subject_user_id, requested_by_user_id, state, expires_at) "
            "VALUES (:s, :r, 'pending', :exp)"
        ),
        {"s": subject_id, "r": requester_id, "exp": datetime.now(UTC) + timedelta(days=7)},
    )
    await session.commit()

    with pytest.raises(DBAPIError) as pending_only:
        await _scrub()
    assert "no approved anonymisation request" in str(pending_only.value), (
        "a pending request was enough to scrub; the two-person rule is the "
        "whole control and pending means one person"
    )
    await session.rollback()

    await session.execute(
        text(
            "UPDATE user_anonymisation_requests "
            "SET state = 'approved', approved_by_user_id = :a, approved_at = now() "
            "WHERE subject_user_id = :s"
        ),
        {"a": approver_id, "s": subject_id},
    )
    await session.commit()

    assert await _scrub() == 1
    await session.commit()

    ip, user_agent = await _client_details(session, row_id)
    assert ip is None and user_agent is None

    diff = (
        await session.execute(
            text("SELECT diff::text FROM audit_logs WHERE id = :id"), {"id": row_id}
        )
    ).scalar_one()
    assert "before@example.com" in diff, (
        "the scrub rewrote diff. It must not: diff carries the evidentiary "
        "record, and what it retains is stated in the processing procedure "
        "rather than quietly erased here"
    )


async def test_a_shadow_table_cannot_forge_the_owner_check(
    session: AsyncSession,
) -> None:
    """The layer-two test's own blind spot, closed.

    ``pg_has_role`` is asked about the owner of ``audit_logs``, and until the
    trigger pinned its search_path that name was resolved with the CALLER's.
    Postgres searches the temp schema first and TEMP is granted to PUBLIC, so
    a caller holding UPDATE created a temp table called ``audit_logs``, and
    the check then asked whether they were a member of their own role. It said
    yes. Measured before the fix: the forged scrub went through and the client
    details were erased.

    The layer-two test above passed throughout, because it never made a temp
    table. A guard is only tested against the attacks somebody thought of.

    Two things stop it and each was checked alone, because two defences that
    cover for each other are two defences nobody has verified. Removing the
    schema qualification and keeping the pinned path: still refused. Removing
    ``pg_temp`` from the path and keeping the qualification: still refused.
    Removing both: the forged scrub goes through, which is what makes this
    test worth having.

    ``pg_temp`` is the part that is easy to leave out and looks fine without.
    PostgreSQL searches the temporary schema first for a relation unless the
    path names it, so ``SET search_path = pg_catalog, public`` does not
    demote it; it just does not mention it. Measured on 17.2 with two
    otherwise identical functions: without ``pg_temp`` the function read the
    caller's temp table, with it the real one.
    """
    await _require_app_role(session)
    actor = await make_user(session)
    await session.commit()
    row_id = await _audit_row(session, actor_id=actor.id)

    before = await _client_details(session, row_id)
    assert before[0] is not None and before[1] is not None, (
        "precondition failed: the row must carry client details, or the "
        "UPDATE below changes nothing and proves nothing"
    )

    await session.execute(text(f"GRANT UPDATE ON audit_logs TO {APP_ROLE}"))
    await session.commit()
    try:
        await session.execute(text(f"SET ROLE {APP_ROLE}"))
        await session.execute(
            text("CREATE TEMP TABLE audit_logs (shadow int)")
        )
        await session.execute(text("SELECT set_config('trusca.audit_scrub','on',false)"))
        with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
            await session.execute(
                text(
                    "UPDATE public.audit_logs SET ip = NULL, user_agent = NULL "
                    "WHERE id = :id"
                ),
                {"id": row_id},
            )
            await session.commit()
        assert "append-only" in str(excinfo.value)
        await session.rollback()
    finally:
        await session.execute(text("RESET ROLE"))
        await session.execute(text(f"REVOKE UPDATE ON audit_logs FROM {APP_ROLE}"))
        await session.commit()

    after = await _client_details(session, row_id)
    assert after == before, "the client details did not survive the shadowed scrub"
