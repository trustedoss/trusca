"""
Integration tests for the cross-project scans listing — Step 4.

Endpoint:
  - GET /v1/scans                  List scans across every project the actor
                                    can see.

The single-project listing (`GET /v1/projects/{id}/scans`) is covered in
`test_scans_api.py`; this file pins the team-scope clamp + status filter.
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
        pytest.skip("DATABASE_URL not set — skip cross-project scans tests")
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
            f"alembic upgrade head failed; cross-project scans tests cannot run\n"
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


async def test_cross_project_scans_without_auth_returns_401(client: AsyncClient) -> None:
    response = await client.get("/v1/scans")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Team-scoped clamp
# ---------------------------------------------------------------------------


async def test_developer_only_sees_own_team_scans(client: AsyncClient) -> None:
    """Two teams; the developer must see only the scans from their own team."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        my_team = await make_team(session, organization=org)
        other_team = await make_team(session, organization=org)
        my_user = await make_user(session)
        await make_membership(session, user=my_user, team=my_team, role="developer")

        my_project = await make_project(session, team=my_team)
        other_project = await make_project(session, team=other_team)

        my_scan = await make_scan(session, project=my_project, status="succeeded")
        await make_scan(session, project=other_project, status="succeeded")

    headers = _bearer_for(my_user)
    response = await client.get("/v1/scans", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    visible_ids = {item["id"] for item in body["items"]}
    assert str(my_scan.id) in visible_ids
    # The other team's scan is NOT in the list (team clamp).
    assert all(item["project_id"] == str(my_project.id) for item in body["items"])


async def test_super_admin_sees_all_scans_across_teams(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)
        admin = await make_user(session, is_superuser=True)
        project_a = await make_project(session, team=team_a)
        project_b = await make_project(session, team=team_b)
        scan_a = await make_scan(session, project=project_a, status="succeeded")
        scan_b = await make_scan(session, project=project_b, status="succeeded")

    headers = _bearer_for(admin)
    response = await client.get("/v1/scans", headers=headers, params={"size": 100})
    assert response.status_code == 200, response.text
    body = response.json()
    visible_ids = {item["id"] for item in body["items"]}
    assert str(scan_a.id) in visible_ids
    assert str(scan_b.id) in visible_ids


async def test_user_with_no_memberships_sees_empty_page(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        # User with NO memberships and not a super-admin.
        loner = await make_user(session)
        # Seed a scan elsewhere so the list isn't empty by accident.
        org = await make_organization(session)
        other_team = await make_team(session, organization=org)
        other_project = await make_project(session, team=other_team)
        await make_scan(session, project=other_project, status="succeeded")

    headers = _bearer_for(loner)
    response = await client.get("/v1/scans", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


# ---------------------------------------------------------------------------
# status filter
# ---------------------------------------------------------------------------


async def test_status_filter_only_returns_matching_scans(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        succeeded = await make_scan(session, project=project, status="succeeded")
        failed = await make_scan(session, project=project, status="failed")

    headers = _bearer_for(user)
    succeeded_response = await client.get(
        "/v1/scans", headers=headers, params={"status": "succeeded"}
    )
    failed_response = await client.get(
        "/v1/scans", headers=headers, params={"status": "failed"}
    )

    assert succeeded_response.status_code == 200, succeeded_response.text
    succeeded_ids = {item["id"] for item in succeeded_response.json()["items"]}
    assert str(succeeded.id) in succeeded_ids
    assert str(failed.id) not in succeeded_ids

    assert failed_response.status_code == 200, failed_response.text
    failed_ids = {item["id"] for item in failed_response.json()["items"]}
    assert str(failed.id) in failed_ids
    assert str(succeeded.id) not in failed_ids


async def test_invalid_status_filter_returns_422_problem(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session, is_superuser=True)

    headers = _bearer_for(user)
    response = await client.get(
        "/v1/scans", headers=headers, params={"status": "totally-not-a-status"}
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Ordering + pagination
# ---------------------------------------------------------------------------


async def test_list_orders_by_created_at_desc(client: AsyncClient) -> None:
    """Most recent scan first across all the actor's projects."""
    import asyncio

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        s1 = await make_scan(session, project=project, status="succeeded")
        await asyncio.sleep(0.01)
        s2 = await make_scan(session, project=project, status="succeeded")
        await asyncio.sleep(0.01)
        s3 = await make_scan(session, project=project, status="succeeded")

    headers = _bearer_for(user)
    response = await client.get("/v1/scans", headers=headers)
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    ids = [item["id"] for item in items]
    # Most recent first.
    assert ids[0] == str(s3.id)
    assert ids[1] == str(s2.id)
    assert ids[2] == str(s1.id)


async def test_pagination_returns_correct_page_size(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        for _ in range(5):
            await make_scan(session, project=project, status="succeeded")

    headers = _bearer_for(user)
    response = await client.get(
        "/v1/scans", headers=headers, params={"page": 1, "size": 2}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["page"] == 1
    assert body["size"] == 2
    assert len(body["items"]) == 2
    # The total reflects all 5 scans created in this test.
    assert body["total"] >= 5


async def test_size_over_cap_returns_422(client: AsyncClient) -> None:
    factory = await _factory(client)
    async with factory() as session:
        user = await make_user(session, is_superuser=True)
    headers = _bearer_for(user)

    response = await client.get("/v1/scans", headers=headers, params={"size": 5000})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_includes_project_name_and_slug(client: AsyncClient) -> None:
    """P1 #5 — every scan row carries denormalised project_name + project_slug.

    The Scans queue UI shows these in place of the first 8 chars of a raw
    UUID. The list endpoint eager-loads ``Scan.project`` and the schema
    factory splices the two fields onto each ScanPublic.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="developer")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")

    headers = _bearer_for(user)
    response = await client.get("/v1/scans", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    matching = [item for item in body["items"] if item["id"] == str(scan.id)]
    assert matching, "expected the newly-created scan in the page"
    item = matching[0]
    assert item["project_name"] == project.name
    assert item["project_slug"] == project.slug
