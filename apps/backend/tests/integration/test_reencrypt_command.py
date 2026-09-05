# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The rotation command an operator actually runs (E22b).

``services/key_rotation_service.py`` is covered by ``test_key_rotation.py``.
This covers the command wrapped around it, which is a separate thing that can
be wrong: what it does with the modes, what it prints, and what it exits with.

The exit code is the part that matters most. The documented sequence says to
confirm zero before removing a key, and an operator who scripts that is
relying on a non-zero exit when something is left. A command that always
returned 0 would read as a finished rotation on every run.

Hardening rule 6: a command with no test that runs it has no test. Importing
its module, or testing only the service it calls, would not have noticed a
command that never reached the service at all.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from scripts.reencrypt_secrets import _main
from tests._db_required import migrate_to_head
from tests._helpers import make_organization, make_project, make_team

pytestmark = pytest.mark.integration

KEY_ENV = "GITHUB_APP_ENCRYPTION_KEY"


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


@pytest.fixture(autouse=True)
def _database_url_for_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """The command reads its own DSN, as an operator's shell would."""
    from core.config import database_url

    monkeypatch.setenv("DATABASE_URL_OWNER", database_url())


@pytest.fixture(autouse=True)
async def _only_this_test_s_ciphertext(session: AsyncSession) -> AsyncIterator[None]:
    """Clear every encrypted column before each test in this file.

    What the command exits with is a statement about the whole database, not
    about one row, and that is the point of it: an operator asks "is anything
    left" and acts on the answer. Asserting on that number requires owning
    everything it counts.

    The database is shared with the rest of the integration suite, which leaves
    rows encrypted under keys generated in other tests. Those are genuinely
    unreadable here, so without this the command is right to exit non-zero and
    the assertion below would be measuring the neighbours.

    Some rows are deleted rather than nulled. ``private_key_encrypted`` is NOT
    NULL, so a credential row cannot be emptied of its ciphertext and still
    exist; the first version of this nulled every column and passed locally
    only because no such row had been created yet.

    Which columns get cleared comes from the registry rather than from a list
    written here, because a list written here goes stale silently: the column
    added for the TOTP secret was not in it, the MFA tests run earlier in the
    same database, and this file then counted their rows as leftovers on a
    missing key. The only decision left per table is whether the row is
    nothing but a credential and should go with it.
    """
    from core.encrypted_columns import ENCRYPTED_COLUMNS

    #: Tables whose rows exist only to hold a credential, and whose ciphertext
    #: column is NOT NULL, so the row cannot outlive it.
    delete_whole_row = {"github_app_credentials", "registry_credentials"}

    for table in sorted({c.table for c in ENCRYPTED_COLUMNS} & delete_whole_row):
        await session.execute(text(f"DELETE FROM {table}"))  # noqa: S608

    # Everything else carries far more than ciphertext, so the column is
    # nulled and the row stays.
    for column in ENCRYPTED_COLUMNS:
        if column.table in delete_whole_row:
            continue
        await session.execute(
            text(f"UPDATE {column.table} SET {column.column} = NULL")  # noqa: S608
        )
    await session.commit()
    yield


async def _project_on_key(session: AsyncSession, key: str, secret: str):  # noqa: ANN202
    """A row written under ``key``, whatever the process is configured with."""
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(
        session, team=team, git_url=f"https://example.com/a/{uuid.uuid4().hex[:10]}"
    )
    project.webhook_secret_encrypted = Fernet(key.encode()).encrypt(
        secret.encode()
    ).decode()
    project.webhook_provider = "github"
    await session.commit()
    return project


async def test_an_unknown_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before anything touches the database.

    A typo in the mode must not silently become the reading one or the writing
    one. Exit 2 rather than 1, matching the other operator commands: 1 is a
    result, 2 is a usage error.
    """
    monkeypatch.setenv("MODE", "rewrit")
    assert await _main() == 2


async def test_no_dsn_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL_OWNER", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MODE", "count")
    assert await _main() == 2


async def test_count_exits_non_zero_while_rows_are_on_an_older_key(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The gate the runbook tells an operator to script.

    If this returned 0 with work outstanding, the documented sequence would
    remove the old key next and the rows would be gone.
    """
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    await _project_on_key(session, old, "carried")
    monkeypatch.setenv(KEY_ENV, f"{new},{old}")
    monkeypatch.setenv("MODE", "count")

    assert await _main() == 1
    err = capsys.readouterr().err
    assert "still on an older key" in err, err
    assert "Do NOT remove a key" in err


async def test_rewrite_then_count_reaches_zero(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The whole sequence through the command rather than the service.

    Ends on the count, because that is what the operator is told to believe,
    and a rewrite that reported success while leaving rows behind is the
    failure this pair exists to catch.
    """
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    project = await _project_on_key(session, old, "carried")
    monkeypatch.setenv(KEY_ENV, f"{new},{old}")

    monkeypatch.setenv("MODE", "rewrite")
    assert await _main() == 0
    assert "rewrote" in capsys.readouterr().out

    monkeypatch.setenv("MODE", "count")
    assert await _main() == 0
    out = capsys.readouterr().out
    assert "Nothing is on an older key" in out, out

    # And the value survived, which no exit code says.
    from core.crypto import decrypt_secret

    await session.refresh(project)
    assert decrypt_secret(project.webhook_secret_encrypted) == "carried"


async def test_a_row_no_key_opens_exits_non_zero_and_says_which(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """What an operator sees after removing a key too early.

    Distinct from the ordinary stale case: running the rewrite cannot fix
    this, so the message has to send them after the missing key rather than
    after the command they just ran.
    """
    stranger = Fernet.generate_key().decode()
    current = Fernet.generate_key().decode()
    await _project_on_key(session, stranger, "lost")
    monkeypatch.setenv(KEY_ENV, current)
    monkeypatch.setenv("MODE", "count")

    assert await _main() == 1
    err = capsys.readouterr().err
    assert "could not be opened by any configured key" in err, err
    assert "GITHUB_APP_ENCRYPTION_KEY" in err
