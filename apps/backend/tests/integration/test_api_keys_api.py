"""
Integration tests for /v1/api-keys — Phase 5 PR #16.

The 4-role matrix (anonymous / developer / team_admin / super_admin) is the
spine of these tests, mirroring tests/integration/admin/test_admin_users_api.py.

  - Anonymous            -> 401 + Problem Details
  - Developer            -> 201 for own project / 403 on team / 403 on org
  - Team Admin           -> 201 for own team / project / 403 on org
  - Super Admin          -> 201 for all three scopes

Plus contract assertions:
  - All 4xx responses use application/problem+json (RFC 7807).
  - POST returns ``raw_key``; GET (list) NEVER returns it.
  - Cross-team revoke is forbidden (404 existence-hide).
  - Validation errors (empty / oversized name, garbage scope) → 422.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip api_keys API tests")
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
            f"alembic upgrade head failed; api_keys API tests cannot run\n"
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
# POST /v1/api-keys — anonymous
# ---------------------------------------------------------------------------


async def test_post_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/api-keys",
        json={"name": "ci", "scope": "org"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 401
    assert "title" in body
    assert "instance" in body


async def test_get_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/api-keys")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_delete_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.delete(f"/v1/api-keys/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# POST /v1/api-keys — super_admin happy paths (org / team / project)
# ---------------------------------------------------------------------------


async def test_post_super_admin_org_scope_returns_201_with_raw_key(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "ci-org", "scope": "org"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scope"] == "org"
    assert body["team_id"] is None
    assert body["project_id"] is None
    raw = body["raw_key"]
    assert isinstance(raw, str) and raw.startswith(body["key_prefix"] + "_")
    # The raw key contains the prefix + underscore + secret.
    assert len(raw) > len(body["key_prefix"]) + 16


async def test_post_super_admin_team_scope_returns_201(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)
        team = await make_team(session, organization=org)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "ci-team", "scope": "team", "team_id": str(team.id)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scope"] == "team"
    assert body["team_id"] == str(team.id)


async def test_post_super_admin_project_scope_returns_201(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "ci-proj", "scope": "project", "project_id": str(project.id)},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["scope"] == "project"
    assert body["project_id"] == str(project.id)
    # team_id is denormalized onto the row at issuance.
    assert body["team_id"] == str(team.id)


# ---------------------------------------------------------------------------
# POST /v1/api-keys — RBAC matrix
# ---------------------------------------------------------------------------


async def test_post_team_admin_org_scope_returns_403(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(user),
        json={"name": "ci-org", "scope": "org"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_team_admin_team_scope_returns_201(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(user),
        json={"name": "ci-team", "scope": "team", "team_id": str(team.id)},
    )
    assert response.status_code == 201, response.text


async def test_post_team_admin_other_team_scope_returns_403(client: AsyncClient) -> None:
    """A team_admin of team A may not issue a team-scoped key for team B."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team_a, role="team_admin")

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(user),
        json={"name": "x", "scope": "team", "team_id": str(team_b.id)},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_developer_org_scope_returns_403(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(user),
        json={"name": "x", "scope": "org"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_developer_team_scope_returns_403(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(user),
        json={"name": "x", "scope": "team", "team_id": str(team.id)},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_developer_project_scope_own_team_returns_201(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(user),
        json={"name": "x", "scope": "project", "project_id": str(project.id)},
    )
    assert response.status_code == 201, response.text


async def test_post_developer_project_scope_foreign_team_returns_403(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team_a, role="developer")
        foreign_project = await make_project(session, team=team_b)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(user),
        json={
            "name": "x",
            "scope": "project",
            "project_id": str(foreign_project.id),
        },
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# POST /v1/api-keys — validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,payload",
    [
        ("rejects_empty_name", {"name": "", "scope": "org"}),
        ("rejects_oversized_name", {"name": "a" * 200, "scope": "org"}),
        ("rejects_unknown_scope", {"name": "x", "scope": "global"}),
        ("rejects_missing_name", {"scope": "org"}),
        ("rejects_missing_scope", {"name": "x"}),
        ("rejects_non_uuid_team_id", {"name": "x", "scope": "team", "team_id": "not-a-uuid"}),
    ],
)
async def test_post_invalid_payload_returns_422_problem(
    client: AsyncClient, label: str, payload: dict[str, str]
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json=payload,
    )
    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 422
    assert body["title"] == "Validation Error"


async def test_post_team_scope_without_team_id_returns_422(client: AsyncClient) -> None:
    """Service-layer scope coherence: scope='team' without team_id → 422."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "x", "scope": "team"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_project_scope_unknown_project_returns_404(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "x", "scope": "project", "project_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/api-keys — pagination + visibility
# ---------------------------------------------------------------------------


async def test_get_super_admin_returns_pagination_envelope(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/api-keys?page=1&page_size=10",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert body["page"] == 1
    assert body["page_size"] == 10


async def test_get_list_never_returns_raw_key(client: AsyncClient) -> None:
    """The list endpoint MUST omit the plaintext (only POST returns raw_key)."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    # Create a key first.
    create_resp = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "list-leak", "scope": "org"},
    )
    assert create_resp.status_code == 201

    list_resp = await client.get(
        "/v1/api-keys?page_size=200",
        headers=_bearer_for(admin),
    )
    assert list_resp.status_code == 200
    body = list_resp.json()
    for item in body["items"]:
        assert "raw_key" not in item
        assert "key_hash" not in item


async def test_get_developer_does_not_see_foreign_team_keys(client: AsyncClient) -> None:
    """Cross-tenant: a developer in team_b does NOT see team_a's project keys."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        project_a = await make_project(session, team=team_a)
        outsider = await make_user(session)
        await make_membership(session, user=outsider, team=team_b, role="developer")

    # Admin issues a project-scoped key for team_a.
    create_resp = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "secret-a", "scope": "project", "project_id": str(project_a.id)},
    )
    assert create_resp.status_code == 201
    foreign_key_id = create_resp.json()["id"]

    # Outsider in team_b lists keys; the foreign key MUST be absent.
    # ``page_size`` max is 200 (api/v1/api_keys.py:Query(le=200)); 500 → 422.
    list_resp = await client.get(
        "/v1/api-keys?page_size=200",
        headers=_bearer_for(outsider),
    )
    assert list_resp.status_code == 200
    ids = {item["id"] for item in list_resp.json()["items"]}
    assert foreign_key_id not in ids


async def test_get_invalid_scope_query_returns_422(client: AsyncClient) -> None:
    """An out-of-enum scope value on the query string fails closed (422)."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/api-keys",
        params={"scope": "global"},
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# DELETE /v1/api-keys/{id}
# ---------------------------------------------------------------------------


async def test_delete_super_admin_returns_204(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    create_resp = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "to-revoke", "scope": "org"},
    )
    assert create_resp.status_code == 201
    key_id = create_resp.json()["id"]

    delete_resp = await client.delete(
        f"/v1/api-keys/{key_id}",
        headers=_bearer_for(admin),
    )
    assert delete_resp.status_code == 204
    assert delete_resp.content == b""


async def test_delete_idempotent_returns_204(client: AsyncClient) -> None:
    """Second delete on an already-revoked key still returns 204."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    create_resp = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "double-delete", "scope": "org"},
    )
    key_id = create_resp.json()["id"]

    first = await client.delete(f"/v1/api-keys/{key_id}", headers=_bearer_for(admin))
    second = await client.delete(f"/v1/api-keys/{key_id}", headers=_bearer_for(admin))
    assert first.status_code == 204
    assert second.status_code == 204


async def test_delete_unknown_id_returns_404_problem(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.delete(
        f"/v1/api-keys/{uuid.uuid4()}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_delete_cross_team_developer_returns_404(client: AsyncClient) -> None:
    """A developer in team_b cannot revoke team_a's key — existence-hide 404."""
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        outsider = await make_user(session)
        await make_membership(session, user=outsider, team=team_b, role="developer")

    create_resp = await client.post(
        "/v1/api-keys",
        headers=_bearer_for(admin),
        json={"name": "x", "scope": "team", "team_id": str(team_a.id)},
    )
    key_id = create_resp.json()["id"]

    response = await client.delete(
        f"/v1/api-keys/{key_id}",
        headers=_bearer_for(outsider),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_delete_invalid_uuid_returns_422(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)

    response = await client.delete(
        "/v1/api-keys/not-a-uuid",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
