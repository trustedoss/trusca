# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Team isolation, asserted once across all four ``/v1/dashboard/*`` GETs.

Each of the four routes (``summary``, ``action-queue``, ``trends``,
``portfolio``) already has its own isolation coverage in its own test file,
built around that route's specific response shape. This file adds one more
layer on top: a single, route-agnostic check that a caller in team A never
sees team B's project id or team id anywhere in the response body, run against
all four routes with the same fixture. None of the route has a path
parameter: what the caller may see is decided entirely by the accessible-
projects scoping each service applies, so this is the whole access-control
story for these routes, parametrized so a scoping regression on any one of
the four cannot land unnoticed just because the other three have their own
dedicated test file.
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
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROBLEM_JSON = "application/problem+json"

pytestmark = pytest.mark.integration

DASHBOARD_ROUTES = [
    "/v1/dashboard/summary",
    "/v1/dashboard/action-queue",
    "/v1/dashboard/trends",
    "/v1/dashboard/portfolio",
]


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip dashboard isolation matrix tests")
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
        pytest.skip(f"alembic upgrade head failed:\n{result.stderr}")


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
    token = create_access_token(subject=str(user.id))
    return {"Authorization": f"Bearer {token}"}


async def _factory(client: AsyncClient):
    app = client._transport.app  # type: ignore[attr-defined]
    factory = getattr(app.state, "session_factory", None)
    if factory is None:
        from core.db import _ensure_state

        factory = _ensure_state(app)
    return factory


async def _critical_finding(session, *, scan_id: uuid.UUID) -> None:
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    session.add(
        ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True, raw_data={})
    )
    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix}",
        source="NVD",
        severity="critical",
        summary="isolation matrix fixture",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id, component_version_id=cv.id, vulnerability_id=vuln.id
        )
    )
    await session.commit()


@pytest.mark.parametrize("path", DASHBOARD_ROUTES)
async def test_route_never_leaks_another_teams_project_or_team_id(
    client: AsyncClient, path: str
) -> None:
    """Team B's identifiers must be entirely absent from team A's response.

    Team B carries a scanned project with an open critical finding, the kind
    of row every one of the four aggregates would otherwise be eager to
    surface (it is exactly what ``action-queue`` and ``summary`` are built to
    highlight), so a caller in team A getting a "boring" empty response is
    not, by itself, proof of isolation. Only the absence of team B's own ids
    from the body is.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team_a = await make_team(session, organization=org)
        team_b = await make_team(session, organization=org)

        user_a = await make_user(session)
        await make_membership(session, user=user_a, team=team_a, role="developer")

        project_b = await make_project(session, team=team_b)
        scan_b = await make_scan(session, project=project_b, status="succeeded")
        await _critical_finding(session, scan_id=scan_b.id)

    headers = _bearer_for(user_a)
    response = await client.get(path, headers=headers)

    assert response.status_code == 200, response.text
    body_text = response.text
    assert str(project_b.id) not in body_text, (
        f"{path} leaked team B's project id to a team A caller"
    )
    assert str(team_b.id) not in body_text, (
        f"{path} leaked team B's team id to a team A caller"
    )
    assert str(scan_b.id) not in body_text, (
        f"{path} leaked team B's scan id to a team A caller"
    )
