"""
Integration tests for project detail HTTP surface — Phase 3 PR #10.

Endpoints:
  - GET /v1/projects/{project_id}/overview
  - GET /v1/projects/{project_id}/components
  - GET /v1/components/{component_id}

We pin the wire format (RFC 7807 envelope on errors, Pydantic shape on
success) and the auth/IDOR guards. Heavier behavioural coverage (sorting,
filtering, search) lives in `tests/unit/test_project_detail_service.py`.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from core.security import create_access_token
from models import User
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip project detail API tests")
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
            f"alembic upgrade head failed; project detail API tests cannot run\n"
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


async def _seed_team_with_user(
    client: AsyncClient, *, role: str = "developer", is_superuser: bool = False
):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
    return org, team, user


async def _seed_scanned_project(client: AsyncClient, *, team_id: uuid.UUID):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (
            await session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
        return project.id, scan.id


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_overview_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/overview")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_components_list_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/components")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_component_detail_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/components/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/overview
# ---------------------------------------------------------------------------


async def test_overview_happy_path_returns_well_formed_payload(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project_id}/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == str(project_id)
    assert body["total_components"] == 0
    # Distributions present even with zero data.
    assert "critical" in body["severity_distribution"]
    assert "forbidden" in body["license_distribution"]
    assert isinstance(body["risk_score"], int | float)
    assert body["risk_score"] == 0.0


async def test_overview_other_team_returns_403_problem(client) -> None:
    _, my_team, my_user = await _seed_team_with_user(client)
    _, other_team, _ = await _seed_team_with_user(client)
    other_project_id, _ = await _seed_scanned_project(client, team_id=other_team.id)
    headers = _bearer_for(my_user)

    response = await client.get(
        f"/v1/projects/{other_project_id}/overview", headers=headers
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_overview_unknown_project_returns_404_problem(client) -> None:
    _, _, admin = await _seed_team_with_user(client, is_superuser=True)
    headers = _bearer_for(admin)
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/overview", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/components
# ---------------------------------------------------------------------------


async def test_components_list_happy_path_paginated(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/components",
        headers=headers,
        params={"limit": 20, "offset": 0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert body["total"] == 0
    assert body["items"] == []


async def test_components_list_invalid_sort_returns_422_problem(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/components",
        headers=headers,
        params={"sort": "BOGUS"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_components_list_other_team_returns_403_problem(client) -> None:
    _, my_team, my_user = await _seed_team_with_user(client)
    _, other_team, _ = await _seed_team_with_user(client)
    other_project_id, _ = await _seed_scanned_project(client, team_id=other_team.id)
    headers = _bearer_for(my_user)

    response = await client.get(
        f"/v1/projects/{other_project_id}/components", headers=headers
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_components_list_limit_over_cap_returns_422(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/components",
        headers=headers,
        params={"limit": 5000},
    )
    # Pydantic Query(le=500) → 422 problem+json.
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/components/{id}
# ---------------------------------------------------------------------------


async def test_component_detail_unknown_id_returns_404_problem(client) -> None:
    _, _, admin = await _seed_team_with_user(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/components/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
