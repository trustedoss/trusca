"""
Integration tests for /v1/users/me/oauth-identities — Chore G.

Contract checks:
  - Anonymous → 401 + Problem Details on both GET and DELETE.
  - GET returns the caller's own identities, sorted oldest-first, with the
    wire fields ``provider_email`` and ``created_at`` (NOT the underlying
    ORM names ``email`` / ``linked_at``).
  - DELETE: caller can unlink their own identity (204).
  - DELETE: cross-user unlink → 404 (existence-hide) with the
    ``urn:trustedoss:problem:oauth_identity_not_found`` ``type`` URI.
  - DELETE: last-method guard → 409 with the
    ``urn:trustedoss:problem:oauth_unlink_blocks_login`` ``type`` URI.
  - Audit row written on successful unlink with hashed provider_user_id.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.security import create_access_token
from models import User
from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

NOT_FOUND_TYPE = "urn:trustedoss:problem:oauth_identity_not_found"
BLOCKS_LOGIN_TYPE = "urn:trustedoss:problem:oauth_unlink_blocks_login"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip oauth-identities API tests")
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
            "alembic upgrade head failed; oauth-identities tests cannot run\n"
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


async def _make_identity(
    factory,
    *,
    user_id: uuid.UUID,
    provider: str = "github",
    email: str | None = None,
    provider_user_id: str | None = None,
):
    from models import OAuthIdentity

    async with factory() as session:
        row = OAuthIdentity(
            user_id=user_id,
            provider=provider,
            provider_user_id=provider_user_id or uuid.uuid4().hex,
            email=email or f"{uuid.uuid4().hex}@example.com",
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def _clear_password(factory, *, user_id: uuid.UUID) -> None:
    """Force the user into OAuth-only by blanking ``hashed_password``."""
    async with factory() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one()
        user.hashed_password = ""
        await session.commit()


# ---------------------------------------------------------------------------
# GET — anonymous + authenticated
# ---------------------------------------------------------------------------


async def test_get_oauth_identities_anonymous_returns_401(
    client: AsyncClient,
) -> None:
    response = await client.get("/v1/users/me/oauth-identities")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_oauth_identities_returns_empty_for_new_user(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.get(
        "/v1/users/me/oauth-identities",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"items": []}


async def test_get_oauth_identities_returns_caller_identities_oldest_first(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    pid_gh = uuid.uuid4().hex
    pid_google = uuid.uuid4().hex
    first = await _make_identity(
        factory,
        user_id=user.id,
        provider="github",
        email="user@gh.example",
        provider_user_id=pid_gh,
    )
    second = await _make_identity(
        factory,
        user_id=user.id,
        provider="google",
        email="user@google.example",
        provider_user_id=pid_google,
    )

    response = await client.get(
        "/v1/users/me/oauth-identities",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    items = body["items"]
    assert [i["id"] for i in items] == [str(first.id), str(second.id)]

    # Wire shape: provider_email + created_at (NOT email + linked_at).
    assert set(items[0].keys()) == {
        "id",
        "provider",
        "provider_user_id",
        "provider_email",
        "created_at",
    }
    assert items[0]["provider"] == "github"
    assert items[0]["provider_email"] == "user@gh.example"
    assert items[0]["provider_user_id"] == pid_gh
    assert items[1]["provider"] == "google"
    assert items[1]["provider_email"] == "user@google.example"


async def test_get_oauth_identities_isolates_users(client: AsyncClient) -> None:
    """Alice cannot see Bob's identities."""
    factory = await _factory(client)
    async with factory() as session:
        alice = await make_user(session)
        bob = await make_user(session)

    await _make_identity(factory, user_id=alice.id, provider="github")
    bob_identity = await _make_identity(
        factory, user_id=bob.id, provider="github"
    )

    alice_resp = await client.get(
        "/v1/users/me/oauth-identities",
        headers=_bearer_for(alice),
    )
    bob_resp = await client.get(
        "/v1/users/me/oauth-identities",
        headers=_bearer_for(bob),
    )
    alice_ids = {i["id"] for i in alice_resp.json()["items"]}
    bob_ids = {i["id"] for i in bob_resp.json()["items"]}
    assert str(bob_identity.id) not in alice_ids
    assert str(bob_identity.id) in bob_ids


# ---------------------------------------------------------------------------
# DELETE — anonymous + happy path
# ---------------------------------------------------------------------------


async def test_delete_oauth_identity_anonymous_returns_401(
    client: AsyncClient,
) -> None:
    response = await client.delete(
        f"/v1/users/me/oauth-identities/{uuid.uuid4()}"
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_delete_oauth_identity_happy_path_returns_204(
    client: AsyncClient,
) -> None:
    """User with password + 2 identities → unlinking one returns 204."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
    first = await _make_identity(factory, user_id=user.id, provider="github")
    await _make_identity(factory, user_id=user.id, provider="google")

    response = await client.delete(
        f"/v1/users/me/oauth-identities/{first.id}",
        headers=_bearer_for(user),
    )
    assert response.status_code == 204, response.text

    # Listing afterwards confirms the row is gone.
    listing = await client.get(
        "/v1/users/me/oauth-identities",
        headers=_bearer_for(user),
    )
    remaining = [i["id"] for i in listing.json()["items"]]
    assert str(first.id) not in remaining


# ---------------------------------------------------------------------------
# DELETE — guards (RFC 7807)
# ---------------------------------------------------------------------------


async def test_delete_unknown_identity_returns_404_with_problem_type(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.delete(
        f"/v1/users/me/oauth-identities/{uuid.uuid4()}",
        headers=_bearer_for(user),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["type"] == NOT_FOUND_TYPE
    assert body["status"] == 404


async def test_delete_someone_elses_identity_returns_404_existence_hide(
    client: AsyncClient,
) -> None:
    """Cross-user delete → 404 with the same shape as 'no row'."""
    factory = await _factory(client)
    async with factory() as session:
        alice = await make_user(session)
        bob = await make_user(session)
    bob_identity = await _make_identity(factory, user_id=bob.id, provider="github")

    response = await client.delete(
        f"/v1/users/me/oauth-identities/{bob_identity.id}",
        headers=_bearer_for(alice),
    )
    assert response.status_code == 404
    body = response.json()
    assert body["type"] == NOT_FOUND_TYPE

    # Bob's identity is still present.
    bob_listing = await client.get(
        "/v1/users/me/oauth-identities",
        headers=_bearer_for(bob),
    )
    bob_ids = {i["id"] for i in bob_listing.json()["items"]}
    assert str(bob_identity.id) in bob_ids


async def test_delete_last_identity_no_password_returns_409_blocks_login(
    client: AsyncClient,
) -> None:
    """OAuth-only user, single identity → 409 with the dedicated type URI."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
    await _clear_password(factory, user_id=user.id)
    only = await _make_identity(factory, user_id=user.id, provider="github")

    response = await client.delete(
        f"/v1/users/me/oauth-identities/{only.id}",
        headers=_bearer_for(user),
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    # Frozen contract — frontend maps this exact URI to its error message.
    assert body["type"] == BLOCKS_LOGIN_TYPE
    assert body["title"] == "Cannot remove last authentication method"
    assert body["status"] == 409
    # RFC 7807 required fields are present.
    for required in ("type", "title", "status", "detail", "instance"):
        assert required in body

    # Identity still exists.
    listing = await client.get(
        "/v1/users/me/oauth-identities",
        headers=_bearer_for(user),
    )
    listing_ids = {i["id"] for i in listing.json()["items"]}
    assert str(only.id) in listing_ids


async def test_delete_one_of_two_no_password_succeeds(
    client: AsyncClient,
) -> None:
    """OAuth-only user with 2 identities can remove one (the other is fallback)."""
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
    await _clear_password(factory, user_id=user.id)
    first = await _make_identity(factory, user_id=user.id, provider="github")
    await _make_identity(factory, user_id=user.id, provider="google")

    response = await client.delete(
        f"/v1/users/me/oauth-identities/{first.id}",
        headers=_bearer_for(user),
    )
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# Audit row contract
# ---------------------------------------------------------------------------


async def test_delete_writes_audit_row_with_hashed_provider_user_id(
    client: AsyncClient,
) -> None:
    """Successful unlink → explicit audit row carries sha256(provider_user_id)."""
    from models import AuditLog

    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    # Per-test stable id so the unique-constraint never collides across runs.
    pid = f"stable-{uuid.uuid4().hex}"
    expected_hash = hashlib.sha256(pid.encode("utf-8")).hexdigest()
    target = await _make_identity(
        factory,
        user_id=user.id,
        provider="google",
        provider_user_id=pid,
    )
    await _make_identity(factory, user_id=user.id, provider="github")

    response = await client.delete(
        f"/v1/users/me/oauth-identities/{target.id}",
        headers=_bearer_for(user),
    )
    assert response.status_code == 204

    async with factory() as session:
        rows = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.target_table == "oauth_identities")
                .where(AuditLog.target_id == str(target.id))
                .where(AuditLog.action == "oauth.identity.unlinked")
            )
        ).scalars().all()
    assert len(rows) == 1
    audit = rows[0]
    assert audit.actor_user_id == user.id
    assert audit.diff["provider"] == "google"
    assert audit.diff["provider_user_id_hash"] == expected_hash
    # The raw provider_user_id is never persisted in the explicit row.
    assert "provider_user_id" not in audit.diff
