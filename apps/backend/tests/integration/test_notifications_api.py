"""
Integration tests for /v1/notifications/* — Chore A2.

Auth matrix:
  - anonymous   → 401 + Problem Details
  - any user    → only sees own rows; one user's id is invisible to another.

Endpoint contract:
  - GET /v1/notifications                     200 list + counts
  - PATCH /v1/notifications/{id}/read         204 idempotent
  - PATCH /v1/notifications/read-all          204 marks all caller's unread
  - GET /v1/notifications/unread-count        200 {count}

All 4xx responses use ``application/problem+json``.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import create_access_token
from models import User
from tests._helpers import make_user

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip notifications API tests")
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
            "alembic upgrade head failed; notifications API tests cannot run\n"
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


async def _create_inapp(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str = "scan_completed",
    title: str = "Scan complete",
    body: str = "Project foo finished scanning.",
    link: str | None = None,
    target_table: str | None = None,
    target_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a notification row directly via the service (test-time helper)."""
    from services.notification_service import create_notification

    row = await create_notification(
        session,
        user_id=user_id,
        kind=kind,
        title=title,
        body=body,
        link=link,
        target_table=target_table,
        target_id=target_id,
    )
    return row.id


# ---------------------------------------------------------------------------
# Anonymous → 401 + Problem Details
# ---------------------------------------------------------------------------


async def test_get_notifications_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/notifications")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 401
    assert "title" in body and "instance" in body


async def test_get_unread_count_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/notifications/unread-count")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_read_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.patch(f"/v1/notifications/{uuid.uuid4()}/read")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_read_all_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.patch("/v1/notifications/read-all")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/notifications — happy path
# ---------------------------------------------------------------------------


async def test_get_notifications_empty_returns_empty_page(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.get("/v1/notifications", headers=_bearer_for(user))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["unread_count"] == 0
    assert body["page"] == 1
    assert body["page_size"] == 20


async def test_get_notifications_returns_caller_rows_only(client: AsyncClient) -> None:
    """One user's GET must not reveal another user's notifications."""
    factory = await _factory(client)
    async with factory() as session:
        alice = await make_user(session)
        bob = await make_user(session)
        bob_id = await _create_inapp(
            session,
            user_id=bob.id,
            title="bob-only",
            body="should never appear in alice's view",
        )

    response = await client.get("/v1/notifications", headers=_bearer_for(alice))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["unread_count"] == 0
    # Sanity: even the id is not echoed.
    raw = response.text
    assert str(bob_id) not in raw


async def test_get_notifications_unread_filter_and_counts(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        _read_id = await _create_inapp(session, user_id=user.id, title="read")
        unread_id = await _create_inapp(session, user_id=user.id, title="unread")

        from services.notification_service import mark_read

        await mark_read(session, user_id=user.id, notification_id=_read_id)

    # unread_only=true returns only the unread row but unread_count stays 1.
    response = await client.get(
        "/v1/notifications?unread_only=true",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["unread_count"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(unread_id)
    assert body["items"][0]["read_at"] is None

    # unread_only=false returns both; unread_count is still 1.
    response_all = await client.get(
        "/v1/notifications?unread_only=false",
        headers=_bearer_for(user),
    )
    body_all = response_all.json()
    assert body_all["total"] == 2
    assert body_all["unread_count"] == 1


async def test_get_notifications_pagination(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        for idx in range(5):
            await _create_inapp(session, user_id=user.id, title=f"n{idx}")

    response = await client.get(
        "/v1/notifications?page=1&page_size=2",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["unread_count"] == 5
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["page_size"] == 2

    response_p3 = await client.get(
        "/v1/notifications?page=3&page_size=2",
        headers=_bearer_for(user),
    )
    body_p3 = response_p3.json()
    assert body_p3["total"] == 5
    assert len(body_p3["items"]) == 1


async def test_get_notifications_invalid_page_size_returns_422(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.get(
        "/v1/notifications?page_size=0",
        headers=_bearer_for(user),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/notifications/unread-count
# ---------------------------------------------------------------------------


async def test_get_unread_count_returns_count(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        await _create_inapp(session, user_id=user.id)
        await _create_inapp(session, user_id=user.id)
        await _create_inapp(session, user_id=user.id)

    response = await client.get(
        "/v1/notifications/unread-count",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {"count": 3}


# ---------------------------------------------------------------------------
# PATCH /v1/notifications/{id}/read
# ---------------------------------------------------------------------------


async def test_patch_read_marks_as_read_and_is_idempotent(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)
        notif_id = await _create_inapp(session, user_id=user.id)

    headers = _bearer_for(user)
    r1 = await client.patch(f"/v1/notifications/{notif_id}/read", headers=headers)
    assert r1.status_code == 204

    # Idempotent — second call also returns 204.
    r2 = await client.patch(f"/v1/notifications/{notif_id}/read", headers=headers)
    assert r2.status_code == 204

    # Unread count drops to 0.
    count = await client.get("/v1/notifications/unread-count", headers=headers)
    assert count.json() == {"count": 0}


async def test_patch_read_other_users_id_returns_404_problem_json(
    client: AsyncClient,
) -> None:
    """Existence-hide: cross-user mark-read must return 404 (not 403)."""
    factory = await _factory(client)
    async with factory() as session:
        alice = await make_user(session)
        bob = await make_user(session)
        bob_notif = await _create_inapp(session, user_id=bob.id)

    response = await client.patch(
        f"/v1/notifications/{bob_notif}/read",
        headers=_bearer_for(alice),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_read_unknown_id_returns_404(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.patch(
        f"/v1/notifications/{uuid.uuid4()}/read",
        headers=_bearer_for(user),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_patch_read_invalid_uuid_returns_422(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.patch(
        "/v1/notifications/not-a-uuid/read",
        headers=_bearer_for(user),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# PATCH /v1/notifications/read-all
# ---------------------------------------------------------------------------


async def test_patch_read_all_marks_all_unread_for_caller_only(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        alice = await make_user(session)
        bob = await make_user(session)
        await _create_inapp(session, user_id=alice.id)
        await _create_inapp(session, user_id=alice.id)
        await _create_inapp(session, user_id=bob.id)

    response = await client.patch(
        "/v1/notifications/read-all",
        headers=_bearer_for(alice),
    )
    assert response.status_code == 204

    alice_count = await client.get(
        "/v1/notifications/unread-count", headers=_bearer_for(alice)
    )
    assert alice_count.json() == {"count": 0}

    # Bob's row is untouched.
    bob_count = await client.get(
        "/v1/notifications/unread-count", headers=_bearer_for(bob)
    )
    assert bob_count.json() == {"count": 1}


async def test_patch_read_all_with_no_unread_returns_204(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session)

    response = await client.patch(
        "/v1/notifications/read-all",
        headers=_bearer_for(user),
    )
    assert response.status_code == 204
