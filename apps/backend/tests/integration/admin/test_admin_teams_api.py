"""
Integration tests for /v1/admin/teams — Phase 4 PR #13.

Same 4-role matrix as the users suite (anonymous / developer / team_admin /
super_admin), plus team-specific contracts:

  - DELETE on a team with active scans -> 422 + team_has_active_scans.
  - Removing the last team_admin while developers remain -> 422 +
    last_team_admin_protected.
  - Slug conflict -> 409.
  - All 4xx responses are application/problem+json.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip admin teams API tests")
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
            f"alembic upgrade head failed; admin teams API tests cannot run\n"
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
# Auth gate
# ---------------------------------------------------------------------------


async def test_list_teams_anonymous_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/admin/teams")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_teams_developer_returns_404(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")

    response = await client.get("/v1/admin/teams", headers=_bearer_for(user))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_teams_team_admin_returns_404(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")

    response = await client.get("/v1/admin/teams", headers=_bearer_for(user))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_super_admin_can_list_teams(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session, is_superuser=True)

    response = await client.get(
        "/v1/admin/teams?page=1&page_size=200",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {item["id"] for item in body["items"]}
    assert str(team.id) in ids


async def test_super_admin_create_team_returns_201_and_audits(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        await make_organization(session)
        admin = await make_user(session, is_superuser=True)

    suffix = unique_suffix()
    response = await client.post(
        "/v1/admin/teams",
        headers=_bearer_for(admin),
        json={
            "name": f"Created Team {suffix}",
            "slug": f"created-{suffix}",
            "description": "From admin API",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == f"created-{suffix}"

    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE actor_user_id = :a AND target_table = 'teams' "
                    "  AND action = 'create'"
                ),
                {"a": str(admin.id)},
            )
        ).scalar_one()
    assert rows >= 1


async def test_super_admin_create_team_duplicate_slug_returns_409(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        await make_organization(session)
        admin = await make_user(session, is_superuser=True)

    headers = _bearer_for(admin)
    suffix = unique_suffix()
    payload = {"name": "X", "slug": f"dup-{suffix}"}

    first = await client.post("/v1/admin/teams", headers=headers, json=payload)
    assert first.status_code == 201

    second = await client.post(
        "/v1/admin/teams",
        headers=headers,
        json={"name": "Y", "slug": f"dup-{suffix}"},
    )
    assert second.status_code == 409
    assert second.headers["content-type"].startswith(PROBLEM_JSON)


async def test_super_admin_update_team_returns_200(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session, is_superuser=True)

    response = await client.patch(
        f"/v1/admin/teams/{team.id}",
        headers=_bearer_for(admin),
        json={"name": "Renamed via API"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Renamed via API"


# ---------------------------------------------------------------------------
# Delete contracts
# ---------------------------------------------------------------------------


async def test_delete_team_with_active_scan_returns_422(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        await make_scan(session, project=project, status="running")
        admin = await make_user(session, is_superuser=True)

    response = await client.delete(
        f"/v1/admin/teams/{team.id}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    body = response.json()
    assert body.get("team_has_active_scans") is True


async def test_delete_team_archives_projects_then_succeeds(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        project = await make_project(session, team=team)
        admin = await make_user(session, is_superuser=True)

    response = await client.delete(
        f"/v1/admin/teams/{team.id}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 204, response.text

    # Audit row for the project archive was emitted before CASCADE.
    factory = await _factory(client)
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE target_table = 'projects' AND target_id = :pid"
                ),
                {"pid": str(project.id)},
            )
        ).scalar_one()
    assert rows >= 1


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


async def test_add_member_returns_200_and_audits(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        target = await make_user(session)
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        f"/v1/admin/teams/{team.id}/members",
        headers=_bearer_for(admin),
        json={"user_id": str(target.id), "role": "developer"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(m["user_id"] == str(target.id) and m["role"] == "developer" for m in body["members"])


async def test_add_member_with_unknown_user_returns_404(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        f"/v1/admin/teams/{team.id}/members",
        headers=_bearer_for(admin),
        json={"user_id": str(uuid.uuid4()), "role": "developer"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_remove_last_team_admin_with_others_returns_422(
    client: AsyncClient,
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin_user = await make_user(session)
        dev_user = await make_user(session)
        await make_membership(session, user=admin_user, team=team, role="team_admin")
        await make_membership(session, user=dev_user, team=team, role="developer")
        admin = await make_user(session, is_superuser=True)

    response = await client.delete(
        f"/v1/admin/teams/{team.id}/members/{admin_user.id}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 422
    body = response.json()
    assert body.get("last_team_admin_protected") is True


async def test_remove_member_when_alone_returns_200(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        admin_user = await make_user(session)
        await make_membership(session, user=admin_user, team=team, role="team_admin")
        admin = await make_user(session, is_superuser=True)

    response = await client.delete(
        f"/v1/admin/teams/{team.id}/members/{admin_user.id}",
        headers=_bearer_for(admin),
    )
    assert response.status_code == 200, response.text
    assert response.json()["members"] == []


# ---------------------------------------------------------------------------
# Adversarial input via the wire (Pydantic validation surface)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"name": "A", "slug": ""},  # empty slug
        {"name": "", "slug": "valid"},  # empty name
        {"name": "X", "slug": "with space"},  # invalid slug
        {"name": "X", "slug": "../etc/passwd"},  # path-like
        {"name": "X", "slug": "javascript:alert(1)"},
        {"name": "X" * 300, "slug": "valid"},  # name too long
        {"name": "X", "slug": "X" * 100},  # slug too long
        {"name": "X", "slug": "valid", "description": "X" * 2000},  # desc too long
    ],
)
async def test_create_team_rejects_invalid_payload_with_422_problem(
    client: AsyncClient, bad_payload: dict
) -> None:
    factory = await _factory(client)
    async with factory() as session:
        await make_organization(session)
        admin = await make_user(session, is_superuser=True)

    response = await client.post(
        "/v1/admin/teams",
        headers=_bearer_for(admin),
        json=bad_payload,
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
