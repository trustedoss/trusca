# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A second factor that both ways in actually stop for.

Two paths open a session: the password form and the OAuth callback. A factor
applied to one of them is not a factor, because whoever wants past it uses the
other. The callback is the one that gets forgotten, since it is a redirect
rather than a request/response pair and it sets its cookie in a helper of its
own.

The failure these guard against is not a wrong answer, it is a session that
exists too early. A cookie set before the code screen means the screen is
advisory: a client that skips it is already signed in.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from core import totp
from core.crypto import encrypt_secret
from services.mfa_service import ENCRYPTION_PURPOSE
from tests._db_required import migrate_to_head
from tests._helpers import make_user, unique_suffix

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app as fastapi_app

    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
async def factory() -> AsyncIterator:
    from core.db import _ensure_state
    from main import app as fastapi_app

    yield getattr(fastapi_app.state, "session_factory", None) or _ensure_state(fastapi_app)


async def _enrolled_user(factory, password: str = "a long enough password 7"):
    """A user with the factor on and a known secret."""
    from core.security import set_password

    secret = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
    async with factory() as session:
        user = await make_user(session, email=f"mfa-{unique_suffix()}@example.com")
        set_password(user, password)
        user.mfa_enabled = True
        user.mfa_secret_encrypted = encrypt_secret(secret, purpose=ENCRYPTION_PURPOSE)
        user.mfa_last_counter = None
        await session.commit()
        return user.id, user.email, secret


async def test_a_password_alone_does_not_open_a_session(client, factory) -> None:
    """The whole point, asserted on the response rather than on a flag.

    202 and no cookie. A test that only checked the status would pass against
    an implementation that also set the cookie, which is the implementation
    that makes the code screen optional.
    """
    _id, email, _secret = await _enrolled_user(factory)

    response = await client.post(
        "/auth/login", json={"email": email, "password": "a long enough password 7"}
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["mfa_required"] is True
    assert body["mfa_token"]
    assert "access_token" not in body
    assert "set-cookie" not in {k.lower() for k in response.headers}, (
        "a session cookie was set before the second factor, so a client that "
        "ignores the code screen is already signed in"
    )


async def test_the_pending_token_opens_nothing_on_its_own(client, factory) -> None:
    """Presented as a bearer token it is worth nothing.

    The type check refuses it, which is asserted at the unit level too. Here it
    is asserted through the app, because that is where somebody would actually
    try it.
    """
    _id, email, _secret = await _enrolled_user(factory)
    started = await client.post(
        "/auth/login", json={"email": email, "password": "a long enough password 7"}
    )
    pending = started.json()["mfa_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {pending}"})

    assert me.status_code == 401, me.text


async def test_a_correct_code_completes_the_sign_in(client, factory) -> None:
    _id, email, secret = await _enrolled_user(factory)
    started = await client.post(
        "/auth/login", json={"email": email, "password": "a long enough password 7"}
    )
    pending = started.json()["mfa_token"]

    done = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": pending, "code": totp.code_at(secret, counter=_now_counter())},
    )

    assert done.status_code == 200, done.text
    assert done.json()["access_token"]
    assert "set-cookie" in {k.lower() for k in done.headers}


async def test_the_same_code_cannot_be_presented_twice(client, factory) -> None:
    """Replay prevention, driven through the route so the write is real.

    The step is recorded on the user row. A service that verified correctly but
    never committed would pass a test that called it directly and fail here,
    which is the point of going through the endpoint.
    """
    _id, email, secret = await _enrolled_user(factory)
    code = totp.code_at(secret, counter=_now_counter())

    first_start = await client.post(
        "/auth/login", json={"email": email, "password": "a long enough password 7"}
    )
    first = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": first_start.json()["mfa_token"], "code": code},
    )
    assert first.status_code == 200, first.text

    second_start = await client.post(
        "/auth/login", json={"email": email, "password": "a long enough password 7"}
    )
    second = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": second_start.json()["mfa_token"], "code": code},
    )

    assert second.status_code >= 400, (
        "the same code was accepted twice, so somebody who observes one has "
        "the rest of its thirty seconds to use it"
    )


async def test_a_pending_token_is_spent_by_the_exchange(client, factory) -> None:
    """And the credential itself is one-shot.

    Replaying it still needs a code, so this is not the difference between
    safe and unsafe. It is that a value which arrives in a redirect fragment,
    and therefore sits in browser history, should stop working once it has
    done its job.
    """
    _id, email, secret = await _enrolled_user(factory)
    started = await client.post(
        "/auth/login", json={"email": email, "password": "a long enough password 7"}
    )
    pending = started.json()["mfa_token"]

    first = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": pending, "code": totp.code_at(secret, counter=_now_counter())},
    )
    assert first.status_code == 200, first.text

    replayed = await client.post(
        "/auth/mfa/verify",
        json={
            "mfa_token": pending,
            "code": totp.code_at(secret, counter=_now_counter() + 1),
        },
    )

    assert replayed.status_code >= 400, "a spent pending token was accepted again"


def _now_counter() -> int:
    import time

    return int(time.time()) // totp.PERIOD_SECONDS


async def test_the_service_commits_the_step_by_itself(factory) -> None:
    """Asserted in a second session, because the route hides the question.

    Through the endpoint, `issue_token_pair` commits the same session moments
    later, so removing the commit inside `verify_second_factor` changes
    nothing observable and a route-level test cannot tell the difference. That
    makes the service's own commit look redundant, and the next person to read
    it may delete it.

    It is not redundant, it is unclaimed. Any future caller that verifies a
    factor without going on to open a session -- a step-up check before a
    destructive action, a re-authentication prompt -- would record nothing, and
    the replay window would silently reopen. Reading the row back through a
    different session is what asks whether the write actually landed.
    """
    from models import User
    from services.mfa_service import verify_second_factor

    user_id, _email, secret = await _enrolled_user(factory)
    code = totp.code_at(secret, counter=_now_counter())

    async with factory() as session:
        user = await session.get(User, user_id)
        await verify_second_factor(session, user=user, code=code)

    async with factory() as reader:
        stored = await reader.get(User, user_id)
        assert stored.mfa_last_counter == _now_counter(), (
            "the accepted step was not persisted by the service, so a caller "
            "that does not commit afterwards leaves the code replayable"
        )
