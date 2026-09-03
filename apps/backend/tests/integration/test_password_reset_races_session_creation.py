# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A session created while a reset commits must not outlive the reset.

The reset revokes every refresh token it can see. Seeing is the problem: the
sweep revokes the rows that exist when its statement runs, and the paths that
create a session insert a brand-new row. A creation that read its inputs before
the sweep and committed after it leaves a live refresh token behind, and the
holder turns that into an access token minted *after* the change, which the
``password_changed_at`` check then accepts. The reset ends up guaranteeing
nothing against somebody actually racing it.

A row lock does not close this by itself: a lock covers rows that exist, and the
row in question does not exist yet. What closes it is making both paths take the
same per-user lock, so the creation either commits before the sweep chooses its
targets or is refused for acting on a credential that has since moved.

Two real sessions on two connections, interleaved deliberately. A mock cannot
fail this test: what is under test is what the database does with two
transactions, not what our code says about it.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import func, select

from core.security import set_password
from models import RefreshToken, User
from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

# The reset runs bcrypt at cost 12, so it takes a few hundred milliseconds even
# with nothing in its way. The wait has to clear that by enough of a margin that
# "still running" means "blocked", not "slow".
_BLOCKED_WAIT_SECONDS = 5.0


def _require_database_url() -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("DATABASE_URL not set: skip reset/session-creation race tests")


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
        pytest.skip(f"alembic upgrade head failed: {result.stderr}")


@pytest.fixture
async def factory() -> AsyncIterator:
    from core.db import _ensure_state
    from main import app as fastapi_app

    yield getattr(fastapi_app.state, "session_factory", None) or _ensure_state(fastapi_app)


@pytest.fixture
def captured_reset_email(monkeypatch):
    from services import password_reset_service as svc

    def _capture(*, plaintext_token, user_email, user_id):
        _capture.last = plaintext_token

    _capture.last = None
    monkeypatch.setattr(svc, "_enqueue_reset_email", _capture)
    return _capture


def _gate_first_commit(session) -> tuple[asyncio.Event, asyncio.Event]:
    """Hold a session between its reads and its COMMIT.

    That gap is the race window. Nothing this session has written is visible to
    the other one until the commit lands, so pausing here is pausing inside the
    window. The seam is on the session instance rather than on a production
    symbol, so the code under test runs exactly as it ships.
    """
    reached = asyncio.Event()
    release = asyncio.Event()
    original = session.commit
    state = {"gated": False}

    async def _gated_commit(*args, **kwargs):
        if not state["gated"]:
            state["gated"] = True
            reached.set()
            await release.wait()
        return await original(*args, **kwargs)

    session.commit = _gated_commit  # type: ignore[method-assign]
    return reached, release


async def _active_refresh_count(factory, user_id) -> int:
    async with factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(RefreshToken)
                    .where(
                        RefreshToken.user_id == user_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                )
            ).scalar_one()
        )


async def _issue_reset_token(factory, email: str, captured) -> str:
    from services.password_reset_service import request_password_reset

    async with factory() as session:
        await request_password_reset(session, email=email)
    assert captured.last, "the reset flow issued no token"
    return str(captured.last)


async def _settle(task: asyncio.Task, seconds: float) -> bool:
    """Give a task the chance to finish. Returns whether it did."""
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    except TimeoutError:
        return False
    except Exception:
        # A task that raised has finished; its exception is the caller's to read.
        return True
    return True


async def test_a_rotation_racing_the_reset_leaves_no_live_session(
    factory, captured_reset_email
) -> None:
    """Rotation reaches its INSERT first; the reset must still end everything."""
    from services.auth_service import issue_token_pair, rotate_refresh

    async with factory() as setup:
        user = await make_user(setup)
        email, user_id = user.email, user.id
        _access, refresh, _exp = await issue_token_pair(setup, user=user)

    reset_token = await _issue_reset_token(factory, email, captured_reset_email)

    async with factory() as rotating, factory() as resetting:
        reached, release = _gate_first_commit(rotating)

        rotate_task = asyncio.create_task(rotate_refresh(rotating, raw_refresh=refresh))
        await asyncio.wait_for(reached.wait(), timeout=10.0)
        # The rotation has now read its inputs and holds an uncommitted new row.

        from services.password_reset_service import consume_reset_token

        reset_task = asyncio.create_task(
            consume_reset_token(
                resetting, plaintext_token=reset_token, new_password="raced reset 12345"
            )
        )
        await _settle(reset_task, _BLOCKED_WAIT_SECONDS)

        release.set()
        await asyncio.gather(rotate_task, reset_task, return_exceptions=True)

    assert await _active_refresh_count(factory, user_id) == 0, (
        "a refresh token created during the reset survived it, so the holder can "
        "still mint access tokens the password change was supposed to stop"
    )


async def test_a_login_racing_the_reset_leaves_no_live_session(
    factory, captured_reset_email
) -> None:
    """The same window on the login path, which is the more direct one.

    Somebody who knows the old password can log in as the reset commits. Fixing
    only the rotation would leave this open, and it needs no stolen cookie.
    """
    from services.auth_service import authenticate, issue_token_pair
    from services.password_reset_service import consume_reset_token

    password = "the old password 12"
    async with factory() as setup:
        user = await make_user(setup)
        set_password(user, password)
        await setup.commit()
        email, user_id = user.email, user.id

    reset_token = await _issue_reset_token(factory, email, captured_reset_email)

    async with factory() as logging_in, factory() as resetting:
        who = await authenticate(logging_in, email=email, password=password)
        assert who is not None, "the fixture password does not authenticate"

        reached, release = _gate_first_commit(logging_in)
        login_task = asyncio.create_task(issue_token_pair(logging_in, user=who))
        await asyncio.wait_for(reached.wait(), timeout=10.0)

        reset_task = asyncio.create_task(
            consume_reset_token(
                resetting, plaintext_token=reset_token, new_password="raced login 12345"
            )
        )
        finished_early = await _settle(reset_task, _BLOCKED_WAIT_SECONDS)

        release.set()
        await asyncio.gather(login_task, reset_task, return_exceptions=True)

    assert not finished_early, "the reset swept while a login was in flight"
    assert await _active_refresh_count(factory, user_id) == 0, (
        "a login that started before the reset committed left a live session behind"
    )


async def test_a_rotation_after_the_reset_is_refused(factory, captured_reset_email) -> None:
    """The other order, which must stay a plain refusal.

    Serialising the two paths is only half of it: once the reset has committed,
    a rotation presenting the revoked token has to be turned away rather than
    waved through, and it must not quietly mint a replacement.
    """
    from services.auth_service import RefreshReuseDetected, issue_token_pair, rotate_refresh
    from services.password_reset_service import consume_reset_token

    async with factory() as setup:
        user = await make_user(setup)
        email, user_id = user.email, user.id
        _access, refresh, _exp = await issue_token_pair(setup, user=user)

    reset_token = await _issue_reset_token(factory, email, captured_reset_email)

    async with factory() as resetting:
        await consume_reset_token(
            resetting, plaintext_token=reset_token, new_password="after the reset 12"
        )

    async with factory() as rotating:
        with pytest.raises(RefreshReuseDetected):
            await rotate_refresh(rotating, raw_refresh=refresh)

    assert await _active_refresh_count(factory, user_id) == 0


async def test_a_login_that_verified_the_old_password_cannot_mint_after_the_reset(
    factory, captured_reset_email
) -> None:
    """The order the lock alone cannot save: the reset gets there first.

    Serialising makes the loser wait, and a login that waits then proceeds
    inserts its row *after* the sweep has run. So waiting is not enough; on the
    far side of the lock the path has to notice that the credential it checked
    is no longer the user's credential, and refuse.
    """
    from services.auth_service import authenticate, issue_token_pair
    from services.password_reset_service import consume_reset_token

    password = "the old password 34"
    async with factory() as setup:
        user = await make_user(setup)
        set_password(user, password)
        await setup.commit()
        email, user_id = user.email, user.id

    reset_token = await _issue_reset_token(factory, email, captured_reset_email)

    async with factory() as logging_in:
        who = await authenticate(logging_in, email=email, password=password)
        assert who is not None, "the fixture password does not authenticate"

        # The reset lands in the gap between the credential check and the
        # session being written. Nothing is held here, so no lock is involved.
        async with factory() as resetting:
            await consume_reset_token(
                resetting, plaintext_token=reset_token, new_password="reset won the race 12"
            )

        with pytest.raises(Exception) as refusal:
            await issue_token_pair(logging_in, user=who)

    assert "credential" in str(refusal.value).lower() or refusal.type.__name__ in {
        "StaleCredential",
        "InvalidCredentials",
    }, f"the refusal should name the reason, got {refusal.type.__name__}: {refusal.value}"
    assert await _active_refresh_count(factory, user_id) == 0, (
        "a login holding a password the reset has replaced still opened a session"
    )


async def test_the_reset_locks_the_user_before_it_sweeps(factory, captured_reset_email) -> None:
    """The lock has to be held before the sweep chooses its targets.

    Ordering that the reset gets for free today: autoflush sends the pending
    user UPDATE just before the sweep statement runs. Free is not the same as
    guaranteed. Turn autoflush off, as a session configured elsewhere in the
    codebase could be, and the user UPDATE slides to commit time, which is after
    the sweep has already picked its rows. The window this whole file is about
    reopens, quietly, from a change nobody would connect to password resets.

    So the flush is explicit in the service, and this pins it: with autoflush
    off, the statement touching ``users`` must still precede the one touching
    ``refresh_tokens``.
    """
    from sqlalchemy import event

    from services.auth_service import issue_token_pair
    from services.password_reset_service import consume_reset_token

    async with factory() as setup:
        user = await make_user(setup)
        email, user_id = user.email, user.id
        await issue_token_pair(setup, user=user)

    reset_token = await _issue_reset_token(factory, email, captured_reset_email)

    order: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        head = " ".join(statement.split()[:4]).lower()
        if not head.startswith("update"):
            return
        if "users" in head:
            order.append("users")
        elif "refresh_tokens" in head:
            order.append("refresh_tokens")

    async with factory() as resetting:
        resetting.autoflush = False
        # ``get_bind()`` on an AsyncSession already hands back the sync Engine
        # the async one wraps, which is what carries the DBAPI events.
        sync_engine = resetting.get_bind()
        event.listen(sync_engine, "before_cursor_execute", _record)
        try:
            await consume_reset_token(
                resetting, plaintext_token=reset_token, new_password="ordered writes 12"
            )
        finally:
            event.remove(sync_engine, "before_cursor_execute", _record)

    assert "users" in order and "refresh_tokens" in order, f"expected both updates, saw {order}"
    assert order.index("users") < order.index("refresh_tokens"), (
        "the sweep ran before the user row was locked, so a session created "
        f"concurrently would not have been in range: {order}"
    )
    assert await _active_refresh_count(factory, user_id) == 0


async def test_an_oauth_only_user_signs_in_normally(factory) -> None:
    """The lock must not turn away an account that never had a password typed.

    An OAuth signup stores the bcrypt of a random string rather than leaving the
    column empty, so the re-check has a real value to compare and finds it
    unchanged. Worth executing rather than reasoning about: if the comparison
    ever became one between two absent values, this is the test that would go
    on passing for the wrong reason, so it asserts the session was actually
    opened rather than only that nothing raised.
    """
    from services.oauth_service import _issue_token_pair_in_session

    async with factory() as session:
        user = await make_user(session)
        user_id = user.id
        access, refresh, _expires = await _issue_token_pair_in_session(session, user=user)

    assert access and refresh
    assert await _active_refresh_count(factory, user_id) == 1


async def test_an_oauth_callback_racing_a_reset_is_refused(factory, captured_reset_email) -> None:
    """And it must turn one away when the password moved under it.

    A password reset on an account that also signs in with OAuth is a real
    combination: the account has a usable password precisely because a reset can
    set one. The callback holds a user it read before the reset committed, so it
    goes through the same refusal as a login would.
    """
    from services.auth_service import StaleCredential
    from services.oauth_service import _issue_token_pair_in_session
    from services.password_reset_service import consume_reset_token

    async with factory() as setup:
        user = await make_user(setup)
        email, user_id = user.email, user.id

    reset_token = await _issue_reset_token(factory, email, captured_reset_email)

    async with factory() as completing:
        who = (
            await completing.execute(select(User).where(User.id == user_id))
        ).scalar_one()

        async with factory() as resetting:
            await consume_reset_token(
                resetting, plaintext_token=reset_token, new_password="oauth raced 12345"
            )

        with pytest.raises(StaleCredential):
            await _issue_token_pair_in_session(completing, user=who)

    assert await _active_refresh_count(factory, user_id) == 0


async def test_a_user_deleted_mid_request_gets_no_session(factory) -> None:
    """The row is gone by the time the lock is granted.

    An admin deleting an account while that account is signing in is the
    ordinary way to reach this. Refusing matters for the same reason the rest of
    this file does: the row that would be inserted belongs to nobody, and
    nothing would ever revoke it.
    """
    from services.auth_service import StaleCredential, issue_token_pair

    async with factory() as setup:
        user = await make_user(setup)
        user_id = user.id

    async with factory() as signing_in:
        who = (await signing_in.execute(select(User).where(User.id == user_id))).scalar_one()

        async with factory() as deleting:
            row = (await deleting.execute(select(User).where(User.id == user_id))).scalar_one()
            await deleting.delete(row)
            await deleting.commit()

        with pytest.raises(StaleCredential):
            await issue_token_pair(signing_in, user=who)


async def test_an_absent_stored_credential_is_refused_not_waved_through(factory) -> None:
    """Two absent values must not compare equal and read as "unchanged".

    Unreachable through the column today, which is NOT NULL. Reached here from
    the other side: the caller holds a user whose hash is absent. The branch is
    defence for a schema that stops guaranteeing the value, and the direction it
    fails in is the whole point of it, so it is executed rather than argued for.
    """
    from services.auth_service import StaleCredential, lock_user_for_session_write

    async with factory() as session:
        user = await make_user(session)
        # Detached first: the column really is NOT NULL, so leaving the object
        # attached would have autoflush try to write the None and fail on the
        # constraint instead of reaching the branch under test.
        session.expunge(user)
        user.hashed_password = None  # type: ignore[assignment]

        with pytest.raises(StaleCredential):
            await lock_user_for_session_write(session, user)


async def test_a_refresh_token_whose_subject_is_not_a_uuid_is_refused(factory) -> None:
    """`sub` is signed, but signed does not mean well-formed.

    It reaches a UUID column, and the lock is taken by that value, so it is
    parsed before use rather than handed to the database to reject.
    """
    from core.security import create_refresh_token
    from services.auth_service import InvalidRefreshToken, rotate_refresh

    token, _jti, _exp = create_refresh_token(subject="not-a-uuid")

    async with factory() as session:
        with pytest.raises(InvalidRefreshToken):
            await rotate_refresh(session, raw_refresh=token)


async def test_a_refresh_token_for_a_deleted_user_is_refused(factory) -> None:
    """No user means no lock to take, so the rotation cannot be serialised."""
    import uuid as _uuid

    from core.security import create_refresh_token
    from services.auth_service import InvalidRefreshToken, rotate_refresh

    token, _jti, _exp = create_refresh_token(subject=str(_uuid.uuid4()))

    async with factory() as session:
        with pytest.raises(InvalidRefreshToken):
            await rotate_refresh(session, raw_refresh=token)


async def test_a_refresh_row_belonging_to_another_user_is_refused(factory) -> None:
    """The signed subject and the stored row must agree.

    The lock is taken on the subject in the token. If the row named by the same
    ``jti`` belongs to somebody else, rotating it would write one user's row
    while holding another user's lock, which is exactly the serialisation this
    change exists to establish.
    """
    from core.security import create_refresh_token, hash_refresh_token
    from models import RefreshToken as RefreshTokenModel
    from services.auth_service import InvalidRefreshToken, rotate_refresh

    async with factory() as setup:
        subject = await make_user(setup)
        other = await make_user(setup)
        token, jti, expires = create_refresh_token(subject=str(subject.id))
        setup.add(
            RefreshTokenModel(
                user_id=other.id,
                jti=jti,
                token_hash=hash_refresh_token(token),
                parent_jti=None,
                expires_at=expires,
            )
        )
        await setup.commit()

    async with factory() as session:
        with pytest.raises(InvalidRefreshToken):
            await rotate_refresh(session, raw_refresh=token)
