"""
Integration tests for the policy-gate HTTP surface — Phase 5 PR #17.

Endpoints under test:

  - GET  /v1/projects/{project_id}/gate-result
  - POST /v1/scans/{scan_id}/post-pr-comment

We drive the real ASGI app with httpx and assert the wire format. The
gate-result endpoint accepts JWT tokens (via ``create_access_token``); the
API-key code path is covered by the unit tests on ``core/api_key_auth.py``.

RFC 7807 contract: every 4xx response carries
``Content-Type: application/problem+json``.
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
        pytest.skip("DATABASE_URL not set — skip policy_gate API tests")
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
            f"alembic upgrade head failed; policy_gate API tests cannot run\n"
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


async def _seed_team_and_user(client: AsyncClient, *, role: str = "developer"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
    return org, team, user


async def _seed_project(client: AsyncClient, *, team_id: uuid.UUID):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        project = await make_project(session, team=team)
        project_id = project.id
    return project_id


async def _seed_succeeded_scan(client: AsyncClient, *, project_id: uuid.UUID) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status="succeeded")
        scan_id = scan.id
    return scan_id


# ---------------------------------------------------------------------------
# GET /v1/projects/{project_id}/gate-result
# ---------------------------------------------------------------------------


async def test_gate_result_unauthenticated_returns_401_problem(client) -> None:
    project_id = uuid.uuid4()
    response = await client.get(f"/v1/projects/{project_id}/gate-result")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_gate_result_member_with_no_scan_returns_pass(client) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "pass"
    assert body["reason"] is None
    assert body["scan_id"] is None
    assert body["critical_cve_count"] == 0
    assert body["forbidden_license_count"] == 0
    assert body["project_id"] == str(project_id)
    assert "evaluated_at" in body


async def test_gate_result_member_with_succeeded_scan_returns_pass_with_scan_id(
    client,
) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "pass"
    assert body["scan_id"] == str(scan_id)


async def test_gate_result_non_team_member_returns_404_existence_hide(client) -> None:
    """Cross-team callers must NOT learn whether a project exists."""
    _, team_a, _ = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team_a.id)

    _, _, outsider = await _seed_team_and_user(client)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=_bearer_for(outsider),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_gate_result_unknown_project_returns_404(client) -> None:
    _, _, user = await _seed_team_and_user(client)
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/gate-result",
        headers=_bearer_for(user),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# POST /v1/scans/{scan_id}/post-pr-comment
# ---------------------------------------------------------------------------


async def test_post_pr_comment_dry_run_returns_body_preview(client) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 42,
            "dry_run": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["comment_id"] is None
    assert body["comment_url"] is None
    assert "TrustedOSS" in body["body_preview"]
    assert body["gate"] in ("pass", "fail")


async def test_post_pr_comment_unknown_scan_returns_404(client) -> None:
    _, _, user = await _seed_team_and_user(client)
    response = await client.post(
        f"/v1/scans/{uuid.uuid4()}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_pr_comment_outsider_returns_404_existence_hide(client) -> None:
    _, team_a, _ = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team_a.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    _, _, outsider = await _seed_team_and_user(client)

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(outsider),
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_pr_comment_invalid_repo_slug_returns_422_problem(client) -> None:
    _, team, user = await _seed_team_and_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_succeeded_scan(client, project_id=project_id)

    response = await client.post(
        f"/v1/scans/{scan_id}/post-pr-comment",
        headers=_bearer_for(user),
        json={
            # Path-traversal attempt — must be rejected by the schema.
            "repo_full_name": "../etc/passwd",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_pr_comment_unauthenticated_returns_401(client) -> None:
    response = await client.post(
        f"/v1/scans/{uuid.uuid4()}/post-pr-comment",
        json={
            "repo_full_name": "trustedoss/portal",
            "pr_number": 1,
            "dry_run": True,
        },
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
