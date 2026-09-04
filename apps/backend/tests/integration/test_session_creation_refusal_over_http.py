# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""The refusal reaches the caller as a refusal, not as a 500.

`lock_user_for_session_write` raises `StaleCredential` when a password change
lands between a request checking a credential and writing the session. Nothing
in the app maps `AuthError` globally, so an unhandled one falls through to the
catch-all handler and comes back as a 500. The service-level tests in
`test_password_reset_races_session_creation.py` prove the refusal happens; these
prove the two routers that can provoke it turn it into the answer a client can
act on.

The trigger is injected rather than raced. What is under test here is the
handler, and racing the real thing through HTTP would make the test's outcome
depend on timing while telling us nothing more about the branch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from tests._db_required import migrate_to_head

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


async def test_login_answers_401_when_the_credential_moved_mid_request(
    client: AsyncClient, monkeypatch
) -> None:
    """401 and problem+json, not a 500.

    The person at the form has just had their password changed under them. A
    500 tells them the product is broken; a 401 sends them back to the form,
    where the new password works.
    """
    from tests._helpers import unique_suffix

    email = f"stale-{unique_suffix()}@example.com"
    password = "a sufficiently long password 1"

    register = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "S"},
    )
    assert register.status_code in (200, 201), register.text

    from services import auth_service

    async def _moved(session, user):
        raise auth_service.StaleCredential("the password changed while this request was in flight")

    monkeypatch.setattr(auth_service, "lock_user_for_session_write", _moved)

    response = await client.post("/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 401
    # Named, not just 401: a wrong password answers 401 too, and this test
    # would pass on that alone while the branch it exists for was gone.
    assert body["title"] == "Credential No Longer Valid", body
    assert "set-cookie" not in {k.lower() for k in response.headers}, (
        "a refused login must not leave a refresh cookie behind"
    )


async def test_the_oauth_callback_sends_the_user_back_to_sign_in(
    client: AsyncClient, monkeypatch
) -> None:
    """The callback answers in redirects, so the refusal has to be one too.

    Returning problem+json here would strand the browser on a JSON document
    mid-sign-in. It goes to the front end's failure route like the callback's
    other refusals.
    """
    from api.v1 import oauth as oauth_router
    from services.auth_service import StaleCredential

    async def _moved(*args, **kwargs):
        raise StaleCredential("the password changed while this request was in flight")

    monkeypatch.setattr(oauth_router, "complete_oauth", _moved)

    response = await client.get(
        "/auth/oauth/github/callback",
        params={"code": "irrelevant", "state": "irrelevant"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 307), response.text
    location = response.headers.get("location", "")
    assert "oauth_failed" in location, location
    assert "set-cookie" not in {k.lower() for k in response.headers}, (
        "a refused callback must not leave a refresh cookie behind"
    )
