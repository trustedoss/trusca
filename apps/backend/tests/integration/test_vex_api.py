"""
Integration tests for the VEX export HTTP surface — v2.1 Track A (A1).

Endpoint:
  - GET /v1/projects/{project_id}/vex?format=openvex|cyclonedx

Pins:
  - Both formats return 200 with the right Content-Type + filename.
  - Outsiders see 404 (existence-hide) — never 403.
  - Anonymous → 401.
  - Bad format → 422 problem+json (Pydantic Literal validator).
  - Super admin bypasses the team check.
"""

from __future__ import annotations

import json
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
        pytest.skip("DATABASE_URL not set — skip vex API tests")
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
            f"alembic upgrade head failed; vex API tests cannot run\n"
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


async def _seed(client: AsyncClient, *, role: str = "developer", is_superuser: bool = False):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
    return team, user, project


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_vex_without_auth_returns_401(client: AsyncClient) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/vex")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path — both formats
# ---------------------------------------------------------------------------


async def test_vex_openvex_default_format(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project.id}/vex", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="vex-{project.slug}.openvex.json"'
    )
    parsed = json.loads(response.text)
    assert parsed["@context"] == "https://openvex.dev/ns/v0.2.0"
    assert parsed["statements"] == []


async def test_vex_cyclonedx_returns_bom(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/vex",
        headers=headers,
        params={"format": "cyclonedx"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="vex-{project.slug}.vex.cdx.json"'
    )
    parsed = json.loads(response.text)
    assert parsed["bomFormat"] == "CycloneDX"
    assert parsed["specVersion"] == "1.5"


# ---------------------------------------------------------------------------
# IDOR / RBAC
# ---------------------------------------------------------------------------


async def test_vex_other_team_returns_404_existence_hide(client: AsyncClient) -> None:
    """Outsiders see 404 — same response shape as a missing project."""
    _, _, target_project = await _seed(client)
    _, outsider, _ = await _seed(client)
    headers = _bearer_for(outsider)

    response = await client.get(
        f"/v1/projects/{target_project.id}/vex",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
    assert response.json()["title"] == "Project Not Found"


async def test_vex_unknown_project_returns_404(client: AsyncClient) -> None:
    _, admin, _ = await _seed(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/vex",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_vex_super_admin_bypasses_team_check(client: AsyncClient) -> None:
    _, _, target_project = await _seed(client)
    _, admin, _ = await _seed(client, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{target_project.id}/vex",
        headers=headers,
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Format validation
# ---------------------------------------------------------------------------


async def test_vex_unknown_format_returns_422(client: AsyncClient) -> None:
    _, user, project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project.id}/vex",
        headers=headers,
        params={"format": "totally-bogus"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
