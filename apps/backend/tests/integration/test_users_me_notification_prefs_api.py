"""
Integration tests for /v1/users/me/notification-prefs — Chore A2.

Contract checks:
  - Anonymous → 401 + Problem Details.
  - GET first call creates the default row (email/in_app on, slack/teams off).
  - PUT round-trips all four toggles.
  - PUT is full-row, not partial — missing fields → 422.
  - PUT body fields like ``user_id`` / ``id`` are ignored: the service is
    keyed off the JWT's user_id, so any cross-user write attempt via body
    is silently no-op'd against the actual caller.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip notification-prefs API tests")
    return url


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    _require_database_url()
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(
            "alembic upgrade head failed; notification-prefs tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


def _bearer_for(user: User) -> dict[str, str]:
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


# ---------------------------------------------------------------------------
# Anonymous → 401
# ---------------------------------------------------------------------------


async def test_get_prefs_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/users/me/notification-prefs")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_put_prefs_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.put(
        "/v1/users/me/notification-prefs",
        json={
            "email_enabled": True,
            "slack_enabled": False,
            "teams_enabled": False,
            "in_app_enabled": True,
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET — creates default row
# ---------------------------------------------------------------------------


async def test_get_prefs_first_call_returns_defaults(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.get(
        "/v1/users/me/notification-prefs",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "email_enabled": True,
        "slack_enabled": False,
        "teams_enabled": False,
        "in_app_enabled": True,
    }


async def test_get_prefs_second_call_returns_existing_row(
    client: AsyncClient,
) -> None:
    """The second GET must succeed (no PK collision on default re-insert)."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    headers = _bearer_for(user)
    r1 = await client.get("/v1/users/me/notification-prefs", headers=headers)
    r2 = await client.get("/v1/users/me/notification-prefs", headers=headers)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json() == r2.json()


# ---------------------------------------------------------------------------
# PUT — full row
# ---------------------------------------------------------------------------


async def test_put_prefs_round_trips_all_toggles(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    headers = _bearer_for(user)
    # Chore O / M3 — in_app_enabled cannot be disabled via this PUT.
    # The other channels are individually opt-out.
    response = await client.put(
        "/v1/users/me/notification-prefs",
        headers=headers,
        json={
            "email_enabled": False,
            "slack_enabled": True,
            "teams_enabled": True,
            "in_app_enabled": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "email_enabled": False,
        "slack_enabled": True,
        "teams_enabled": True,
        "in_app_enabled": True,
    }

    # GET round-trips.
    fresh = await client.get("/v1/users/me/notification-prefs", headers=headers)
    assert fresh.json() == body


async def test_put_prefs_missing_fields_returns_422(client: AsyncClient) -> None:
    """PUT is full-row: a partial body is rejected at validation."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.put(
        "/v1/users/me/notification-prefs",
        headers=_bearer_for(user),
        json={"email_enabled": False},  # missing slack/teams/in_app
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_put_prefs_extra_user_id_in_body_is_ignored(
    client: AsyncClient,
) -> None:
    """A caller-supplied ``user_id`` must NOT route the write to another user.

    The endpoint is keyed off the JWT's ``actor.id``; Pydantic strips
    unknown fields and the service ignores anything outside the schema.
    """
    factory = await _factory(client)
    async with factory() as session:
        alice = await make_user(session)
        bob = await make_user(session)

    headers_alice = _bearer_for(alice)
    response = await client.put(
        "/v1/users/me/notification-prefs",
        headers=headers_alice,
        json={
            # Attacker-controlled body field — must be ignored.
            "user_id": str(bob.id),
            "email_enabled": False,
            "slack_enabled": True,
            "teams_enabled": True,
            "in_app_enabled": True,  # Chore O / M3 — cannot be disabled
        },
    )
    assert response.status_code == 200

    # Alice's prefs reflect the change.
    alice_fresh = await client.get(
        "/v1/users/me/notification-prefs", headers=headers_alice
    )
    assert alice_fresh.json()["email_enabled"] is False
    assert alice_fresh.json()["slack_enabled"] is True

    # Bob's prefs are untouched (still defaults).
    bob_fresh = await client.get(
        "/v1/users/me/notification-prefs", headers=_bearer_for(bob)
    )
    assert bob_fresh.json() == {
        "email_enabled": True,
        "slack_enabled": False,
        "teams_enabled": False,
        "in_app_enabled": True,
    }


async def test_put_prefs_isolated_between_users(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        alice = await make_user(session)
        bob = await make_user(session)

    await client.put(
        "/v1/users/me/notification-prefs",
        headers=_bearer_for(alice),
        json={
            "email_enabled": False,
            "slack_enabled": False,
            "teams_enabled": False,
            "in_app_enabled": True,  # Chore O / M3 — cannot be disabled
        },
    )
    await client.put(
        "/v1/users/me/notification-prefs",
        headers=_bearer_for(bob),
        json={
            "email_enabled": True,
            "slack_enabled": True,
            "teams_enabled": False,
            "in_app_enabled": True,
        },
    )

    alice_get = await client.get(
        "/v1/users/me/notification-prefs", headers=_bearer_for(alice)
    )
    bob_get = await client.get(
        "/v1/users/me/notification-prefs", headers=_bearer_for(bob)
    )
    assert alice_get.json()["email_enabled"] is False
    assert alice_get.json()["in_app_enabled"] is True  # always-on
    assert bob_get.json()["email_enabled"] is True
    assert bob_get.json()["slack_enabled"] is True
    assert bob_get.json()["in_app_enabled"] is True


# ---------------------------------------------------------------------------
# Chore O / M3 — In-app channel cannot be disabled
# ---------------------------------------------------------------------------


async def test_put_prefs_in_app_disabled_returns_422(client: AsyncClient) -> None:
    """In-app delivery is always-on. A direct PUT cannot opt out.

    Closes the M3 finding from the Chore O security review: the frontend
    documents the in-app switch as "rendered but disabled", but the
    backend previously accepted ``in_app_enabled=False`` and silently
    disabled the inbox. The 422 + RFC 7807 problem-details guard restores
    the contract.
    """
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.put(
        "/v1/users/me/notification-prefs",
        headers=_bearer_for(user),
        json={
            "email_enabled": True,
            "slack_enabled": False,
            "teams_enabled": False,
            "in_app_enabled": False,
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["type"] == "urn:trustedoss:problem:notification_in_app_required"
    assert "in-app" in body["detail"].lower()


async def test_put_prefs_in_app_disabled_does_not_persist(
    client: AsyncClient,
) -> None:
    """The 422 guard runs before the service write — the row stays at defaults."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    headers = _bearer_for(user)
    await client.put(
        "/v1/users/me/notification-prefs",
        headers=headers,
        json={
            "email_enabled": True,
            "slack_enabled": True,
            "teams_enabled": True,
            "in_app_enabled": False,  # rejected — should not write
        },
    )
    # State is still defaults (no row, or first GET-induced default row).
    fresh = await client.get(
        "/v1/users/me/notification-prefs", headers=headers
    )
    assert fresh.json()["in_app_enabled"] is True
    # Other channels were not flipped on either.
    assert fresh.json()["slack_enabled"] is False
    assert fresh.json()["teams_enabled"] is False
