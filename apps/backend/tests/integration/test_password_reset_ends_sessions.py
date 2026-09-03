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

    headers = {"Authorization": f"Bearer {create_access_token(subject=str(user_id))}"}
    before = await client.get("/v1/users/me", headers=headers)
    assert before.status_code == 200, "the token should work before the reset"

    await _reset_password(
        client,
        email=email,
        new_password="a replacement passphrase 9",
        captured=captured_reset_email,
    )

    after = await client.get("/v1/users/me", headers=headers)

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

    fresh = {"Authorization": f"Bearer {create_access_token(subject=str(user_id))}"}
    response = await client.get("/v1/users/me", headers=fresh)

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
    response = await client.get("/v1/users/me", headers=headers)

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
