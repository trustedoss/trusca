"""
Integration tests for the Scan HTTP surface — Phase 2 PR #7 / PR #8.

Endpoints:
  - POST /v1/projects/{project_id}/scans   Trigger a scan (PR #8: enqueues
                                            via tasks.enqueue_scan)
  - GET  /v1/scans/{scan_id}                Read one scan (IDOR-safe)
  - GET  /v1/projects/{project_id}/scans    List scans for a project

PR #7 contract: the trigger persists status='queued'.
PR #8 contract: the trigger also enqueues a Celery task and stores the
returned task id on `scan.celery_task_id`. We assert the wire shape
(ScanPublic with the `metadata` field, not `scan_metadata`) and the
partial-unique-index 409 contract.
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
        pytest.skip("DATABASE_URL not set — skip scans API tests")
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
            f"alembic upgrade head failed; scans API tests cannot run\n"
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
    """
    Return the AsyncSession factory backing the FastAPI app.

    httpx's ASGITransport does not run lifespan events by default, so
    `app.state.session_factory` may be unset. `core.db._ensure_state` builds
    it lazily and is idempotent.
    """
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _seed(
    client: AsyncClient,
    *,
    role: str = "developer",
    is_superuser: bool = False,
):
    """Seed organization + team + user (+ membership) + project. Returns ids."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_superuser=is_superuser)
        if not is_superuser:
            await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
    return team, user, project


async def _seed_scan(client: AsyncClient, *, project_id: uuid.UUID, status: str = "succeeded"):
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Project

        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one()
        scan = await make_scan(session, project=project, status=status)
        return scan.id


# ---------------------------------------------------------------------------
# POST /v1/projects/{id}/scans — trigger
# ---------------------------------------------------------------------------


async def test_developer_can_trigger_scan_in_own_team(client) -> None:
    team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source", "metadata": {"git_ref": "main"}},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["project_id"] == str(project.id)
    assert body["status"] == "queued"
    assert body["progress_percent"] == 0
    # PR #8: trigger_scan now enqueues via tasks.enqueue_scan and stores the
    # returned task id (UUID string from Celery).
    assert isinstance(body["celery_task_id"], str)
    assert len(body["celery_task_id"]) > 0
    # The schema must surface `metadata` (the API field) — not the ORM
    # attribute name `scan_metadata`. This is the smoke test for the
    # serialization_alias contract in schemas/scan.py::ScanPublic.
    assert body["metadata"] == {"git_ref": "main"}
    assert "scan_metadata" not in body


async def test_trigger_scan_default_kind_is_source(client) -> None:
    team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={},
    )
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "source"


async def test_concurrent_trigger_returns_409_problem(client) -> None:
    """Partial unique index gate: second trigger while one is queued = 409."""
    team, user, project = await _seed(client, role="developer")
    headers = _bearer_for(user)

    first = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert first.status_code == 202

    second = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert second.status_code == 409
    assert second.headers["content-type"].startswith(PROBLEM_JSON)
    body = second.json()
    assert body["title"] == "Scan Already In Progress"
    assert body["status"] == 409


async def test_trigger_scan_other_team_returns_403(client) -> None:
    _, target_user, target_project = await _seed(client, role="developer")
    _, outsider, _ = await _seed(client, role="developer")
    headers = _bearer_for(outsider)

    response = await client.post(
        f"/v1/projects/{target_project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_trigger_scan_unknown_project_returns_404(client) -> None:
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.post(
        f"/v1/projects/{uuid.uuid4()}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_trigger_scan_super_admin_bypasses_team_check(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.post(
        f"/v1/projects/{target_project.id}/scans",
        headers=headers,
        json={"kind": "container"},
    )
    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "container"


async def test_trigger_scan_without_auth_returns_401(client) -> None:
    response = await client.post(
        f"/v1/projects/{uuid.uuid4()}/scans",
        json={"kind": "source"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_inactive_user_cannot_trigger_scan(client) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session, is_active=False)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
    headers = _bearer_for(user)

    response = await client.post(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        json={"kind": "source"},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/scans/{scan_id}
# ---------------------------------------------------------------------------


async def test_get_scan_same_team_returns_200(client) -> None:
    team, user, project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=project.id)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/scans/{scan_id}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(scan_id)
    assert body["project_id"] == str(project.id)
    # Wire field uses `metadata` not `scan_metadata`
    assert "metadata" in body
    assert "scan_metadata" not in body


async def test_get_scan_other_team_returns_403(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=target_project.id)
    _, outsider, _ = await _seed(client, role="developer")
    headers = _bearer_for(outsider)

    response = await client.get(f"/v1/scans/{scan_id}", headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_scan_super_admin_bypasses_team_check(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    scan_id = await _seed_scan(client, project_id=target_project.id)
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/scans/{scan_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(scan_id)


async def test_get_scan_unknown_id_returns_404(client) -> None:
    _, admin, _ = await _seed(client, role="developer", is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(f"/v1/scans/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_get_scan_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/scans/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/scans
# ---------------------------------------------------------------------------


async def test_list_scans_for_project_returns_paginated_list(client) -> None:
    team, user, project = await _seed(client, role="developer")
    # Seed three scans (terminal status so the partial unique index doesn't
    # block the inserts).
    for _ in range(3):
        await _seed_scan(client, project_id=project.id, status="succeeded")

    headers = _bearer_for(user)
    response = await client.get(
        f"/v1/projects/{project.id}/scans",
        headers=headers,
        params={"page": 1, "size": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1
    assert body["size"] == 2
    assert body["total"] >= 3
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert "metadata" in item
        assert "scan_metadata" not in item


async def test_list_scans_for_project_other_team_returns_403(client) -> None:
    _, _, target_project = await _seed(client, role="developer")
    _, outsider, _ = await _seed(client, role="developer")
    headers = _bearer_for(outsider)

    response = await client.get(f"/v1/projects/{target_project.id}/scans", headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
