"""Integration test — ``scripts/create_super_admin.py`` ensure-active recovery.

Background (2026-05-26):
``create_super_admin.py`` used to no-op when the row already existed *and* was
a super admin. That hid a real failure mode: if anything later flipped
``is_active = False`` (an operator deactivating themselves by mistake, a
stray integration test against the dev DB tripping ``deactivate_user``,
etc.), the operator had no first-party recovery path — re-running the
bootstrap script silently did nothing. The fix turns the existing-and-already-
super-admin branch into an ensure-active branch that lifts a stale
deactivation.

This test exercises the recovery path against a real Postgres (CLAUDE.md core
rule #1 — no SQLite). It seeds an inactive super-admin row, runs the script
as a subprocess (the same way ``install.sh`` does), and asserts the row is
flipped back to active without disturbing the password hash.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip create_super_admin tests")
    return url


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    url = _require_database_url()
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _delete_user(factory: async_sessionmaker, email: str) -> None:
    from models import User

    async with factory() as session:
        await session.execute(delete(User).where(User.email == email))
        await session.commit()


def _run_script(*, email: str, password: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ADMIN_EMAIL": email, "ADMIN_PASSWORD": password}
    return subprocess.run(
        [sys.executable, "-m", "scripts.create_super_admin"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.asyncio
async def test_reactivates_inactive_super_admin(
    session_factory: async_sessionmaker,
) -> None:
    """Re-running the script on a deactivated super admin must flip is_active=true."""
    from core.security import hash_password
    from models import User

    email = f"recover-{uuid.uuid4().hex[:8]}@example.com"
    original_hash = hash_password("OriginalPassword12345")
    try:
        async with session_factory() as session:
            session.add(
                User(
                    email=email,
                    hashed_password=original_hash,
                    full_name="Super Admin",
                    is_active=False,  # the stale deactivation we want to recover from
                    is_superuser=True,
                )
            )
            await session.commit()

        # Re-run the bootstrap script with a different password — recovery must
        # NOT touch the password hash (CWE-521 — we only correct the deactivation,
        # we don't silently rotate the credential).
        result = _run_script(email=email, password="DifferentPasswordIgnored12")
        assert result.returncode == 0, (
            f"script failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "reactivated" in result.stdout.lower()

        async with session_factory() as session:
            row = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one()
            assert row.is_active is True
            assert row.is_superuser is True
            # Password hash unchanged — the recovery path is non-destructive.
            assert row.hashed_password == original_hash
    finally:
        await _delete_user(session_factory, email)


@pytest.mark.asyncio
async def test_noop_on_already_active_super_admin(
    session_factory: async_sessionmaker,
) -> None:
    """An already-active super admin row stays untouched on re-run."""
    from core.security import hash_password
    from models import User

    email = f"noop-{uuid.uuid4().hex[:8]}@example.com"
    original_hash = hash_password("OriginalPassword12345")
    try:
        async with session_factory() as session:
            session.add(
                User(
                    email=email,
                    hashed_password=original_hash,
                    is_active=True,
                    is_superuser=True,
                )
            )
            await session.commit()

        result = _run_script(email=email, password="DifferentPasswordIgnored12")
        assert result.returncode == 0
        assert "noop" in result.stdout.lower()

        async with session_factory() as session:
            row = (
                await session.execute(select(User).where(User.email == email))
            ).scalar_one()
            assert row.is_active is True
            assert row.hashed_password == original_hash
    finally:
        await _delete_user(session_factory, email)


@pytest.mark.asyncio
async def test_rejects_existing_non_super_admin(
    session_factory: async_sessionmaker,
) -> None:
    """If the email belongs to a non-super-admin, the script refuses to act."""
    from core.security import hash_password
    from models import User

    email = f"plain-{uuid.uuid4().hex[:8]}@example.com"
    try:
        async with session_factory() as session:
            session.add(
                User(
                    email=email,
                    hashed_password=hash_password("OriginalPassword12345"),
                    is_active=True,
                    is_superuser=False,
                )
            )
            await session.commit()

        result = _run_script(email=email, password="AnyPasswordOver12chars")
        assert result.returncode == 1
        assert "not super_admin" in result.stderr.lower()
    finally:
        await _delete_user(session_factory, email)
