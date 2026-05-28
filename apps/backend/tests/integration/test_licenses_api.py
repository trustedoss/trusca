"""
Integration tests for the licenses HTTP surface — Phase 3 PR #12.

Endpoints:

  - GET /v1/projects/{project_id}/licenses
  - GET /v1/license_findings/{finding_id}

Pins the wire format (RFC 7807 envelope on errors), the auth gate, and the
3xx vs 4xx contract. Heavier behavioural coverage (filter combinations,
search escape, sort) lives in :file:`tests/unit/test_license_service.py`.
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
        pytest.skip("DATABASE_URL not set — skip licenses API tests")
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
            "alembic upgrade head failed; licenses API tests cannot run\n"
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


async def _seed_license_finding(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    spdx_id: str | None = None,
    name: str | None = None,
    category: str = "allowed",
) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        from models import Component, ComponentVersion, License, LicenseFinding

        suffix = uuid.uuid4().hex[:10]
        cname = f"pkg-{suffix}"
        purl = f"pkg:npm/{cname}"
        component = Component(purl=purl, package_type="npm", name=cname)
        session.add(component)
        await session.commit()
        await session.refresh(component)

        cv = ComponentVersion(
            component_id=component.id,
            version="1.0.0",
            purl_with_version=f"{purl}@1.0.0",
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)

        lic = License(
            spdx_id=spdx_id or f"SPDX-{suffix}",
            name=name or f"License {suffix}",
            category=category,
        )
        session.add(lic)
        await session.commit()
        await session.refresh(lic)

        lf = LicenseFinding(
            scan_id=scan_id,
            component_version_id=cv.id,
            license_id=lic.id,
            kind="concluded",
            source_path=f"path/{suffix}",
            raw_data={},
        )
        session.add(lf)
        await session.commit()
        await session.refresh(lf)
        return lf.id


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_list_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/licenses")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_detail_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/license_findings/{uuid.uuid4()}")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/licenses
# ---------------------------------------------------------------------------


async def test_list_happy_path_empty(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/licenses",
        headers=headers,
        params={"limit": 20, "offset": 0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []
    # All four buckets always present so the chart renders a stable axis.
    assert set(body["distribution"].keys()) == {
        "forbidden",
        "conditional",
        "allowed",
        "unknown",
    }


async def test_list_returns_seeded_finding(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_license_finding(
        client, scan_id=scan_id, spdx_id="API-MIT", category="allowed"
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/licenses",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(finding_id)
    assert body["items"][0]["spdx_id"] == "API-MIT"
    assert body["distribution"]["allowed"] == 1


async def test_list_multivalue_category_query_param(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    await _seed_license_finding(client, scan_id=scan_id, category="forbidden")
    await _seed_license_finding(client, scan_id=scan_id, category="conditional")
    await _seed_license_finding(client, scan_id=scan_id, category="allowed")
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/licenses",
        headers=headers,
        params=[("category", "forbidden"), ("category", "conditional")],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    cats = {row["category"] for row in body["items"]}
    assert cats == {"forbidden", "conditional"}


async def test_list_invalid_sort_returns_422_problem(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team.id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/licenses",
        headers=headers,
        params={"sort": "BOGUS"},
    )
    # FastAPI's regex pattern catches this at validation; surface should be
    # the canonical 422 RFC 7807 envelope.
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_list_cross_team_returns_403(client) -> None:
    _, team_a, _ = await _seed_team_with_user(client)
    project_id, _ = await _seed_scanned_project(client, team_id=team_a.id)

    # Outsider user is in a different team.
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_b = await make_team(session, organization=org)
        outsider = await make_user(session)
        await make_membership(session, user=outsider, team=team_b, role="developer")

    headers = _bearer_for(outsider)
    response = await client.get(
        f"/v1/projects/{project_id}/licenses",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/license_findings/{id}
# ---------------------------------------------------------------------------


async def test_detail_happy_path(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team.id)
    finding_id = await _seed_license_finding(
        client, scan_id=scan_id, spdx_id="API-Apache-2.0", category="allowed"
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/license_findings/{finding_id}",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(finding_id)
    assert body["spdx_id"] == "API-Apache-2.0"
    assert body["category"] == "allowed"
    # affected_components carries at least the seeded cv.
    assert len(body["affected_components"]) >= 1


async def test_detail_unknown_id_returns_404(client) -> None:
    _, _, user = await _seed_team_with_user(client)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/license_findings/{uuid.uuid4()}",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_detail_cross_team_existence_hide_returns_404(client) -> None:
    """A finding visible to team A is 404 (not 403) for team B."""
    _, team_a, _ = await _seed_team_with_user(client)
    _, scan_id = await _seed_scanned_project(client, team_id=team_a.id)
    finding_id = await _seed_license_finding(client, scan_id=scan_id)

    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_b = await make_team(session, organization=org)
        outsider = await make_user(session)
        await make_membership(session, user=outsider, team=team_b, role="developer")

    headers = _bearer_for(outsider)
    response = await client.get(
        f"/v1/license_findings/{finding_id}",
        headers=headers,
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
