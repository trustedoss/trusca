# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A password reset ends the sessions that were already open.

Driven over HTTP against a real database, because the predicate passing in
isolation says nothing about whether the check is reached: it lives inside
``_load_current_user``, behind a permission cache, on a path every
authenticated request shares. A unit test of the comparison would have passed
just as happily with the call site missing.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.security import create_access_token
from models import PasswordResetToken, User
from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip password-reset session tests")
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
        pytest.skip(f"alembic upgrade head failed: {result.stderr}")


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


@pytest.fixture(autouse=True)
def captured_reset_email(monkeypatch):
    """Intercept the reset email so the test can read the plaintext token.

    The token is only ever emailed and is stored as a bcrypt hash, so this is
    the same seam ``test_auth_password_reset.py`` uses rather than a second
    way in.
    """
    from services import password_reset_service as svc

    def _noop(*, plaintext_token, user_email, user_id):
        _noop.last = {"plaintext_token": plaintext_token, "user_id": user_id}

    _noop.last = None
    monkeypatch.setattr(svc, "_enqueue_reset_email", _noop)
    return _noop


async def _reset_password(client: AsyncClient, *, email: str, new_password: str, captured) -> None:
    """Run the real reset flow end to end, which is what rotates the hash."""
    response = await client.post("/auth/forgot-password", json={"email": email})
    assert response.status_code in (200, 202, 204), response.text

    plaintext = (captured.last or {}).get("plaintext_token")
    assert plaintext, "the reset flow sent no token"

    confirmed = await client.post(
        "/auth/reset-password",
        json={"token": plaintext, "new_password": new_password},
    )
    assert confirmed.status_code in (200, 204), confirmed.text


async def test_a_token_issued_before_the_reset_stops_working(
    client: AsyncClient, captured_reset_email
) -> None:
    """The defect: this token kept working for the rest of its 30 minutes."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        email = user.email
        user_id = user.id

    # Backdated a minute, which is what a stolen token looks like: minted at
    # some earlier point in its 30-minute life, not in the same second as the
    # reset. A token minted inside that second deliberately survives (see
    # `password_change_invalidates`), so issuing one here would test the
    # tie-breaking rule rather than the behaviour this exists for.
    issued_at = datetime.now(UTC) - timedelta(minutes=1)
    stolen = create_access_token(
        subject=str(user_id), extra_claims={"iat": int(issued_at.timestamp())}
    )
    headers = {"Authorization": f"Bearer {stolen}"}
    before = await client.get("/auth/me", headers=headers)
    assert before.status_code == 200, "the token should work before the reset"

    await _reset_password(
        client,
        email=email,
        new_password="a replacement passphrase 9",
        captured=captured_reset_email,
    )

    after = await client.get("/auth/me", headers=headers)

    assert (
        after.status_code == 401
    ), "the access token minted before the password reset is still accepted"


async def test_a_token_issued_after_the_reset_works(
    client: AsyncClient, captured_reset_email
) -> None:
    """The other half: the person who just reset must be able to sign in.

    A check that refused everything would pass the test above and make the
    product unusable.
    """
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        email = user.email
        user_id = user.id

    await _reset_password(
        client,
        email=email,
        new_password="another replacement 12",
        captured=captured_reset_email,
    )

    # A second later, which is what signing in again actually looks like: the
    # reset returns 204 and sends the user to /login, so the next token is
    # minted by a separate request. A token minted inside the change's own
    # second is refused on purpose, and asserting the opposite here would pin
    # the window an attacker polling /auth/refresh was using.
    later = datetime.now(UTC) + timedelta(seconds=2)
    fresh_token = create_access_token(
        subject=str(user_id), extra_claims={"iat": int(later.timestamp())}
    )
    response = await client.get("/auth/me", headers={"Authorization": f"Bearer {fresh_token}"})

    assert response.status_code == 200, response.text


async def test_an_untouched_user_keeps_their_session(client: AsyncClient) -> None:
    """Nobody else is logged out.

    `password_changed_at` is NULL for every pre-existing row, which the check
    reads as "no opinion". If it read NULL as "changed at epoch" instead, an
    upgrade would end every session in the deployment.
    """
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        user_id = user.id
        assert user.password_changed_at is None

    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user_id))}"}
    response = await client.get("/auth/me", headers=headers)

    assert response.status_code == 200, response.text


async def test_the_reset_stamps_the_column(client: AsyncClient, captured_reset_email) -> None:
    """Read the row back, rather than trusting the service reported success."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        email = user.email
        user_id = user.id

    await _reset_password(
        client,
        email=email,
        new_password="stamped 12345",
        captured=captured_reset_email,
    )

    async with factory() as session:
        subject = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        assert subject.password_changed_at is not None

    async with factory() as session:
        leftover = (
            (
                await session.execute(
                    select(PasswordResetToken).where(
                        PasswordResetToken.user_id == user_id,
                        PasswordResetToken.used_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert not leftover, "the consumed reset token was left usable"


async def test_the_permission_cache_does_not_serve_a_revoked_token(
    client: AsyncClient, captured_reset_email, monkeypatch
) -> None:
    """With the cache on, a warm entry must not carry a pre-reset token.

    The cache is keyed by user id, not by token, so an entry warmed by any
    token was handed to every later token for that user without re-reading the
    row. The stolen token's own pre-reset request warmed it, and the victim's
    new session refilled it on every expiry, so the window was the token's full
    lifetime rather than the cache TTL.

    The default TTL is 0, which is why the other tests here did not see it.
    """
    monkeypatch.setenv("PERMISSION_CACHE_TTL_SECONDS", "300")
    # The cache is process-global and other tests in this run have populated
    # it while the TTL was 0. Start from empty so the warm entry below is the
    # one this test creates, and confirm the knob actually took: reading it
    # here rather than trusting `setenv` is what separates "the cache is off"
    # from "the cache is on and correct", which are the same green otherwise.
    from core.config import permission_cache_ttl_seconds
    from core.security import _principal_cache

    _principal_cache.clear()
    assert permission_cache_ttl_seconds() == 300, "the cache is not actually on"

    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        email = user.email
        user_id = user.id

    issued_at = datetime.now(UTC) - timedelta(minutes=1)
    stolen = create_access_token(
        subject=str(user_id), extra_claims={"iat": int(issued_at.timestamp())}
    )
    headers = {"Authorization": f"Bearer {stolen}"}

    warm = await client.get("/auth/me", headers=headers)
    assert warm.status_code == 200, "the token should work before the reset"
    assert _principal_cache, "nothing was cached, so this proves nothing"

    await _reset_password(
        client,
        email=email,
        new_password="cache eviction passphrase 3",
        captured=captured_reset_email,
    )

    replayed = await client.get("/auth/me", headers=headers)
    assert (
        replayed.status_code == 401
    ), "the permission cache served a token minted before the password change"

    # And the victim's own new session must still work. Minted a second later,
    # because signing in again is a separate request: a token minted inside
    # the change's own second is refused on purpose.
    later = datetime.now(UTC) + timedelta(seconds=2)
    fresh = {
        "Authorization": "Bearer "
        + create_access_token(subject=str(user_id), extra_claims={"iat": int(later.timestamp())})
    }
    assert (await client.get("/auth/me", headers=fresh)).status_code == 200

    replayed_again = await client.get("/auth/me", headers=headers)
    assert replayed_again.status_code == 401, "a refilled cache entry let the revoked token back in"
