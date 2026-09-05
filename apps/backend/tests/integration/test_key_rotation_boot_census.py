# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The boot log says how much of a rotation is left (E22b).

The person who runs the re-encryption and the person who edits the environment
are often not the same person, and the command's output was only ever on the
first one's terminal. The number that decides whether removing a key is safe
therefore also goes where the second one is already looking.

Both branches are asserted. One key means there is nothing to be stale
relative to, and scanning every encrypted column on every boot to report a
guaranteed zero is work that buys nothing; a test that only covered the
counting branch would not notice that guard disappearing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, MutableMapping
from typing import Any

import pytest
import structlog
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_project, make_team

pytestmark = pytest.mark.integration

KEY_ENV = "GITHUB_APP_ENCRYPTION_KEY"
EVENT = "key_rotation.stale_at_boot"


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _boot() -> list[MutableMapping[str, Any]]:
    """Start the app the way uvicorn does, and collect what it logged."""
    import main as m

    with structlog.testing.capture_logs() as captured:
        async with m.lifespan(m.app):
            pass
    return captured


async def test_two_keys_make_the_boot_report_what_is_left(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()

    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(
        session, team=team, git_url=f"https://example.com/a/{uuid.uuid4().hex[:10]}"
    )
    project.webhook_secret_encrypted = (
        Fernet(old.encode()).encrypt(b"carried").decode()
    )
    project.webhook_provider = "github"
    await session.commit()

    monkeypatch.setenv(KEY_ENV, f"{new},{old}")
    entries = [e for e in await _boot() if e.get("event") == EVENT]

    assert entries, "the boot said nothing about the rotation in progress"
    entry = entries[0]
    assert entry["keys"] == 2
    assert entry["stale"] >= 1, entry
    assert "projects.webhook_secret_encrypted" in entry["by_column"], entry


async def test_one_key_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard, asserted rather than assumed.

    Without it the count would run on every boot of every deployment, reading
    every encrypted column to produce a number that cannot be anything but
    zero.
    """
    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())
    entries = [e for e in await _boot() if e.get("event") == EVENT]
    assert entries == [], entries


async def test_a_failure_to_count_does_not_stop_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A census that raises must not be the reason a deployment will not start.

    The number is advisory. Turning it into a boot failure would make an
    operational report into an outage, which is the shape of defect this
    repository has removed elsewhere.
    """
    import services.key_rotation_service as krs

    async def _explode(_session):  # noqa: ANN001, ANN202
        raise RuntimeError("census failed")

    monkeypatch.setattr(krs, "count_stale", _explode)
    monkeypatch.setenv(
        KEY_ENV,
        f"{Fernet.generate_key().decode()},{Fernet.generate_key().decode()}",
    )

    entries = await _boot()
    assert any(e.get("event") == "key_rotation.census_failed" for e in entries), (
        "the failure was swallowed without a trace"
    )
    assert not any(e.get("event") == EVENT for e in entries)
