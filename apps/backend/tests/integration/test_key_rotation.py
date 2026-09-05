# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Rotating the encryption key without losing a row (E22b).

Against a real database rather than a stub, because the part that can go
wrong is the conditional update: a row somebody changed between the read and
the write must be left alone, and that is a property of the statement, not of
the Python around it.

The sequence under test is the one an operator performs. One key, rows
written. Two keys, new one first. Re-encrypt. Confirm nothing is left. Only
then is removing the old key safe, and a test that stops before the last step
would not have shown that the first steps were enough.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """An old key in place, and a new one not yet configured."""
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, old)
    return old, new


async def _project_with_secret(session: AsyncSession, secret: str):  # noqa: ANN202
    from core.crypto import encrypt_secret

    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(
        session, team=team, git_url=f"https://example.com/a/{uuid.uuid4().hex[:10]}"
    )
    project.webhook_secret_encrypted = encrypt_secret(secret)
    project.webhook_provider = "github"
    await session.commit()
    return project


async def test_the_full_rotation_sequence(
    session: AsyncSession, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.crypto import decrypt_secret
    from services.key_rotation_service import count_stale, reencrypt

    old, new = keys
    project = await _project_with_secret(session, "the-shared-secret")

    # Step 1: new key first, old key kept.
    monkeypatch.setenv(KEY_ENV, f"{new},{old}")

    before = await count_stale(session)
    mine = next(
        c for c in before.columns if c.column.label == "projects.webhook_secret_encrypted"
    )
    assert mine.stale >= 1, "the row written under the old key was not counted as stale"

    # Step 2: re-encrypt.
    report = await reencrypt(session)
    assert report.rewritten_total >= 1

    # This test's own row is the one whose fate is being asserted. The other
    # tests in this module leave rows behind that no key here opens, so a
    # global count of unreadable rows says nothing about this sequence.
    from services.key_rotation_service import is_on_current_key

    await session.refresh(project)
    assert is_on_current_key(project.webhook_secret_encrypted, purpose=None), (
        "the row this test wrote is still on the old key after the pass"
    )

    after = await count_stale(session)
    # Rows the other tests here wrote under their own old keys are unreadable
    # from this one and stay stale for good, so the total is not zero and
    # asserting on it would pass or fail on execution order. What has to hold
    # is that nothing READABLE was left behind: that is the condition the
    # operator's removal decision actually turns on.
    left_readable = after.stale_total - after.unreadable_total
    assert left_readable == 0, (
        f"{left_readable} readable row(s) are still on the old key after a "
        "full pass, so an operator following the documented sequence would "
        "remove the old key and lose them"
    )

    # Step 3: the old key goes away. The value still reads.
    monkeypatch.setenv(KEY_ENV, new)
    await session.refresh(project)
    assert decrypt_secret(project.webhook_secret_encrypted) == "the-shared-secret"


async def test_counting_and_rewriting_agree(
    session: AsyncSession, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The number the operator acts on is the number that gets acted on.

    They come from one predicate, so this cannot drift; the test is here
    because the consequence of drift is a report of zero while rows remain,
    and that is not recoverable.
    """
    from services.key_rotation_service import count_stale, reencrypt

    old, new = keys
    await _project_with_secret(session, "s1")
    await _project_with_secret(session, "s2")
    monkeypatch.setenv(KEY_ENV, f"{new},{old}")

    counted = (await count_stale(session)).stale_total
    report = await reencrypt(session)

    # Every row the count called stale is accounted for by the pass: moved,
    # changed underneath it, or openable by no key. Stated as the invariant
    # rather than as equality with `rewritten`, because the database is shared
    # with the other tests here and carries rows written under their keys,
    # which are genuinely unreadable from this one.
    accounted = report.rewritten_total + sum(
        c.raced + c.unreadable for c in report.columns
    )
    assert counted == accounted, (
        f"count said {counted} rows were on an older key; the pass accounted "
        f"for {accounted} (rewrote {report.rewritten_total}, "
        f"unreadable {report.unreadable_total}). A row in neither set is one "
        "the operator is told about and nothing then acts on."
    )


async def test_a_second_pass_does_nothing(
    session: AsyncSession, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safe to repeat, which is what makes it safe to interrupt."""
    from services.key_rotation_service import reencrypt

    old, new = keys
    await _project_with_secret(session, "s")
    monkeypatch.setenv(KEY_ENV, f"{new},{old}")

    first = await reencrypt(session)
    assert first.rewritten_total >= 1
    second = await reencrypt(session)
    assert second.rewritten_total == 0, (
        "a second pass rewrote rows that were already on the newest key"
    )
    # Not `stale_total == 0`: the shared database holds rows from the other
    # tests here, written under their own old keys and unreadable from this
    # one. What this test is about is that nothing readable was moved twice.
    assert second.stale_total == second.unreadable_total, (
        f"{second.stale_total - second.unreadable_total} readable row(s) were "
        "still on an older key after a completed pass"
    )


async def test_a_row_changed_mid_pass_is_not_overwritten(
    session: AsyncSession, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one thing the conditional update exists for.

    Somebody sets a new secret while the pass is running. Rewriting from the
    value read earlier would put back the secret they replaced, and the SCM
    would then hold one this deployment no longer accepts.

    The race is produced by making the pass read the pre-change value while
    the row already holds the new one, which is the state the pass is in
    between its read and its update. Everything after that is the service's
    own statement.

    The first version of this built the UPDATE inside the test and asserted it
    matched nothing. It passed with the condition deleted from the service,
    because the statement it checked was the one it had written.
    """
    from core.crypto import decrypt_secret, encrypt_secret
    from services import key_rotation_service

    old, new_key = keys
    project = await _project_with_secret(session, "the-old-secret")
    monkeypatch.setenv(KEY_ENV, f"{new_key},{old}")

    stale_value = project.webhook_secret_encrypted

    # What somebody did in between: an ordinary application write, which
    # already uses the newest key.
    await session.execute(
        text("UPDATE projects SET webhook_secret_encrypted = :v WHERE id = :pk"),
        {"v": encrypt_secret("the-new-secret"), "pk": project.id},
    )
    await session.commit()

    # What the pass believes it read.
    async def _stale_read(_session, column):  # noqa: ANN001, ANN202
        if column.label != "projects.webhook_secret_encrypted":
            return []
        return [(project.id, stale_value)]

    monkeypatch.setattr(key_rotation_service, "_read_rows", _stale_read)

    report = await key_rotation_service.reencrypt(session)

    mine = next(
        c
        for c in report.columns
        if c.column.label == "projects.webhook_secret_encrypted"
    )
    assert mine.rewritten == 0, (
        "the pass updated a row whose stored value had changed since it read it"
    )
    assert mine.raced == 1, (
        "the row was neither rewritten nor reported as changed underneath, so "
        f"the pass lost track of it: {mine}"
    )

    await session.refresh(project)
    assert decrypt_secret(project.webhook_secret_encrypted) == "the-new-secret", (
        "the rotation put back a secret that had been replaced, so the value "
        "in the SCM is now one this deployment does not accept"
    )


async def test_a_row_no_key_opens_is_reported_not_skipped(
    session: AsyncSession, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable row must be counted and must not stop the pass.

    This is what an operator sees when they removed a key too early. The count
    is how they find out; silently skipping would let the next `count` report
    zero and call the removal safe.
    """
    from services.key_rotation_service import reencrypt

    old, new = keys
    project = await _project_with_secret(session, "readable")

    stranger = Fernet(Fernet.generate_key())
    await session.execute(
        text("UPDATE projects SET webhook_secret_encrypted = :v WHERE id = :pk"),
        {"v": stranger.encrypt(b"lost").decode(), "pk": project.id},
    )
    await session.commit()
    monkeypatch.setenv(KEY_ENV, f"{new},{old}")

    report = await reencrypt(session)
    assert report.unreadable_total >= 1, (
        "a row that no configured key opens was not reported, so the operator "
        "who removed a key too early gets no signal"
    )
