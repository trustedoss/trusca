"""
Integration tests for the Project CRUD HTTP surface — Phase 2 PR #7.

Endpoints under /v1/projects:
  - POST   /v1/projects                Create (role >= developer in target team)
  - GET    /v1/projects                List (team-scoped, paginated)
  - GET    /v1/projects/{id}           Read (IDOR via team membership)
  - PATCH  /v1/projects/{id}           Update (role >= team_admin)
  - DELETE /v1/projects/{id}           Soft-delete (role >= team_admin)

We drive the real ASGI app through httpx and assert the wire format. Database
rows are pre-seeded directly via the helpers in `tests/_helpers.py` because
PR #7 has no admin endpoint to create teams + memberships — that's Phase 4.

RFC 7807 contract: every 4xx response carries Content-Type
`application/problem+json`. We pin the most important ones explicitly so a
regression in the error envelope fails CI.
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
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip projects API tests")
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
            f"alembic upgrade head failed; projects API tests cannot run\n"
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
    """Mint an access token directly so we don't have to register + login."""
    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Auth gate (unauthenticated access)
# ---------------------------------------------------------------------------


async def test_create_project_without_auth_returns_401_problem(client) -> None:
    response = await client.post(
        "/v1/projects",
        json={
            "team_id": str(uuid.uuid4()),
            "name": "n",
            "slug": "s",
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body["status"] == 401
    # Required RFC 7807 fields
    assert "title" in body
    assert "instance" in body


async def test_list_projects_without_auth_returns_401(client) -> None:
    response = await client.get("/v1/projects")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_project_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Helper: seed setup via the FastAPI session factory
# ---------------------------------------------------------------------------


async def _factory(client: AsyncClient):
    """Return the AsyncSession factory; build one lazily if lifespan didn't fire."""
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed_team_with_user(client: AsyncClient, *, role: str, is_superuser: bool = False):
    """Create org + team + user + membership and return them."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
    return org, team, user


async def _seed_project(client: AsyncClient, *, team_id: uuid.UUID, archived: bool = False):
    factory = await _factory(client)
    async with factory() as session:
        # Reload the team in this session to satisfy ORM fk
        from sqlalchemy import select

        from models import Team

        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        project = await make_project(session, team=team, archived=archived)
        # Detach via attribute capture: returning the ORM object hands a session-
        # bound row to the caller, but our caller only reads scalar attrs.
        project_id = project.id
        project_archived_at = project.archived_at
    return project_id, project_archived_at


# ---------------------------------------------------------------------------
# POST /v1/projects
# ---------------------------------------------------------------------------


async def test_developer_can_create_project_in_own_team(client) -> None:
    _, team, user = await _seed_team_with_user(client, role="developer")
    headers = _bearer_for(user)
    slug = f"new-{unique_suffix()}"

    response = await client.post(
        "/v1/projects",
        headers=headers,
        json={"team_id": str(team.id), "name": "New Project", "slug": slug},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == slug
    assert body["team_id"] == str(team.id)
    assert body["visibility"] == "team"
    assert body["archived_at"] is None
    # Server-side fields populated
    assert "id" in body
    assert "created_at" in body
    assert body["created_by_user_id"] == str(user.id)


async def test_create_project_with_existing_slug_returns_409(client) -> None:
    _, team, user = await _seed_team_with_user(client, role="developer")
    headers = _bearer_for(user)
    slug = f"dup-{unique_suffix()}"

    first = await client.post(
        "/v1/projects",
        headers=headers,
        json={"team_id": str(team.id), "name": "A", "slug": slug},
    )
    assert first.status_code == 201

    second = await client.post(
        "/v1/projects",
        headers=headers,
        json={"team_id": str(team.id), "name": "B", "slug": slug},
    )
    assert second.status_code == 409
    assert second.headers["content-type"].startswith(PROBLEM_JSON)


async def test_create_project_outside_actor_team_returns_403(client) -> None:
    _, my_team, user = await _seed_team_with_user(client, role="developer")
    other_org, other_team, _ = await _seed_team_with_user(client, role="developer")
    headers = _bearer_for(user)

    response = await client.post(
        "/v1/projects",
        headers=headers,
        json={
            "team_id": str(other_team.id),
            "name": "X",
            "slug": f"x-{unique_suffix()}",
        },
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_create_project_validation_error_returns_422_problem(
    client,
) -> None:
    _, team, user = await _seed_team_with_user(client, role="developer")
    headers = _bearer_for(user)

    response = await client.post(
        "/v1/projects",
        headers=headers,
        json={
            "team_id": str(team.id),
            "name": "ok",
            "slug": "Bad Slug",  # spaces + uppercase — triggers field_validator
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_inactive_user_cannot_create_project(client) -> None:
    """An inactive user with a valid token must get 401, never 200."""
    factory = await _factory(client)

    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_active=False)
        await make_membership(session, user=user, team=team, role="developer")

    headers = _bearer_for(user)
    response = await client.post(
        "/v1/projects",
        headers=headers,
        json={
            "team_id": str(team.id),
            "name": "n",
            "slug": f"s-{unique_suffix()}",
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects (list)
# ---------------------------------------------------------------------------


async def test_list_projects_paginates(client) -> None:
    _, team, user = await _seed_team_with_user(client, role="developer")
    headers = _bearer_for(user)
    # Seed three projects in this team
    for _ in range(3):
        await _seed_project(client, team_id=team.id)

    response = await client.get(
        "/v1/projects",
        headers=headers,
        params={"team_id": str(team.id), "page": 1, "size": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1
    assert body["size"] == 2
    assert body["total"] >= 3
    assert len(body["items"]) == 2


async def test_list_projects_other_team_id_returns_403(client) -> None:
    _, my_team, user = await _seed_team_with_user(client, role="developer")
    _, other_team, _ = await _seed_team_with_user(client, role="developer")
    headers = _bearer_for(user)

    response = await client.get(
        "/v1/projects",
        headers=headers,
        params={"team_id": str(other_team.id)},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_projects_excludes_archived_by_default(client) -> None:
    _, team, user = await _seed_team_with_user(client, role="developer")
    headers = _bearer_for(user)
    live_pid, _ = await _seed_project(client, team_id=team.id)
    archived_pid, _ = await _seed_project(client, team_id=team.id, archived=True)

    default_response = await client.get(
        "/v1/projects",
        headers=headers,
        params={"team_id": str(team.id)},
    )
    default_ids = {item["id"] for item in default_response.json()["items"]}
    assert str(live_pid) in default_ids
    assert str(archived_pid) not in default_ids

    archived_response = await client.get(
        "/v1/projects",
        headers=headers,
        params={"team_id": str(team.id), "include_archived": True},
    )
    archived_ids = {item["id"] for item in archived_response.json()["items"]}
    assert str(archived_pid) in archived_ids


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}
# ---------------------------------------------------------------------------


async def test_get_project_other_team_returns_403(client) -> None:
    _, my_team, my_user = await _seed_team_with_user(client, role="developer")
    _, other_team, _ = await _seed_team_with_user(client, role="developer")
    target_pid, _ = await _seed_project(client, team_id=other_team.id)
    headers = _bearer_for(my_user)

    response = await client.get(f"/v1/projects/{target_pid}", headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_project_super_admin_can_read_any_team(client) -> None:
    _, team, _ = await _seed_team_with_user(client, role="developer")
    target_pid, _ = await _seed_project(client, team_id=team.id)

    _, _, admin = await _seed_team_with_user(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/projects/{target_pid}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(target_pid)


async def test_get_project_unknown_id_returns_404(client) -> None:
    _, _, admin = await _seed_team_with_user(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)
    response = await client.get(f"/v1/projects/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# PATCH /v1/projects/{id}
# ---------------------------------------------------------------------------


async def test_developer_cannot_patch_project(client) -> None:
    _, team, dev = await _seed_team_with_user(client, role="developer")
    pid, _ = await _seed_project(client, team_id=team.id)
    headers = _bearer_for(dev)

    response = await client.patch(
        f"/v1/projects/{pid}",
        headers=headers,
        json={"name": "renamed"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_team_admin_can_patch_project(client) -> None:
    _, team, admin = await _seed_team_with_user(client, role="team_admin")
    pid, _ = await _seed_project(client, team_id=team.id)
    headers = _bearer_for(admin)

    response = await client.patch(
        f"/v1/projects/{pid}",
        headers=headers,
        json={"name": "renamed", "default_branch": "develop"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "renamed"
    assert body["default_branch"] == "develop"


async def test_super_admin_can_patch_any_team(client) -> None:
    _, team, _ = await _seed_team_with_user(client, role="developer")
    pid, _ = await _seed_project(client, team_id=team.id)

    _, _, admin = await _seed_team_with_user(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.patch(
        f"/v1/projects/{pid}",
        headers=headers,
        json={"description": "by admin"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["description"] == "by admin"


async def test_patch_project_rejects_unknown_field_with_422(client) -> None:
    _, team, admin = await _seed_team_with_user(client, role="team_admin")
    pid, _ = await _seed_project(client, team_id=team.id)
    headers = _bearer_for(admin)

    response = await client.patch(
        f"/v1/projects/{pid}",
        headers=headers,
        json={"slug": "new-slug"},  # extra='forbid' rejects identity fields
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# DELETE /v1/projects/{id}
# ---------------------------------------------------------------------------


async def test_archive_project_returns_204_and_hides_from_default_list(
    client,
) -> None:
    _, team, admin = await _seed_team_with_user(client, role="team_admin")
    pid, _ = await _seed_project(client, team_id=team.id)
    headers = _bearer_for(admin)

    delete_response = await client.delete(f"/v1/projects/{pid}", headers=headers)
    assert delete_response.status_code == 204
    # 204 must have no body
    assert delete_response.content == b""

    # GET still works (admin within the team) but archived_at is set
    fetch = await client.get(f"/v1/projects/{pid}", headers=headers)
    assert fetch.status_code == 200, fetch.text
    assert fetch.json()["archived_at"] is not None

    # Default listing hides archived
    listing = await client.get(
        "/v1/projects",
        headers=headers,
        params={"team_id": str(team.id)},
    )
    ids = {item["id"] for item in listing.json()["items"]}
    assert str(pid) not in ids

    # include_archived=True reveals it
    inclusive = await client.get(
        "/v1/projects",
        headers=headers,
        params={"team_id": str(team.id), "include_archived": True},
    )
    ids_inclusive = {item["id"] for item in inclusive.json()["items"]}
    assert str(pid) in ids_inclusive


async def test_developer_cannot_archive_project(client) -> None:
    _, team, dev = await _seed_team_with_user(client, role="developer")
    pid, _ = await _seed_project(client, team_id=team.id)
    headers = _bearer_for(dev)

    response = await client.delete(f"/v1/projects/{pid}", headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# H-1 regression — cross-team role escalation through HTTP
# ---------------------------------------------------------------------------


async def test_split_membership_user_cannot_patch_developer_team_project(
    client,
) -> None:
    """
    A user who is team_admin in team_a and developer in team_b must NOT be
    able to PATCH a project in team_b. The token-derived `CurrentUser` carries
    `role='team_admin'` (highest across memberships), so this exercises the
    full _load_current_user -> service path, not just the unit-level check.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team_a, role="team_admin")
        await make_membership(session, user=user, team=team_b, role="developer")
        project_a = await make_project(session, team=team_a)
        project_b = await make_project(session, team=team_b)
        project_a_id = project_a.id
        project_b_id = project_b.id

    headers = _bearer_for(user)

    # Negative: PATCH on team_b's project is forbidden — actor is only a
    # developer there, not team_admin.
    forbidden = await client.patch(
        f"/v1/projects/{project_b_id}",
        headers=headers,
        json={"name": "should-not-apply"},
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.headers["content-type"].startswith(PROBLEM_JSON)

    # Positive control: PATCH on team_a's project works — actor really is
    # team_admin there.
    allowed = await client.patch(
        f"/v1/projects/{project_a_id}",
        headers=headers,
        json={"name": "renamed-legit"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["name"] == "renamed-legit"
