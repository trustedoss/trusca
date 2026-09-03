# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``GET /v1/dashboard/summary``: auth, rate limiting, and scoping.

Before W5 this was the one dashboard route (of the four ``/v1/dashboard/*``
GETs) with no rate-limit decorator; every other one already carries
``@limiter.limit(api_read_rate_limit, key_func=_authenticated_user_key)``.
This file is the missing HTTP-contract coverage for the route: it had none of
its own before (only the service layer was tested, in
``tests/unit/services/test_dashboard_service.py``), and it is the surface the
new limiter decorator changes.

The 429 case mirrors ``test_scans_api.py::test_get_scan_is_rate_limited``,
the existing pattern for asserting the ``api_read_rate_limit`` bucket:
tighten the budget via env, exhaust it, and check the RFC 7807 envelope plus
``Retry-After`` on the request that trips it.
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


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set: skip dashboard summary API tests")
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


async def _seed(client: AsyncClient, *, role: str = "developer"):
    """Seed organization + team + user (+ membership) + project. Returns ids."""
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
        project = await make_project(session, team=team)
        await make_scan(session, project=project, status="succeeded")
    return team, user, project


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
        summary="summary fixture",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    session.add(
        VulnerabilityFinding(scan_id=scan_id, component_version_id=cv.id, vulnerability_id=vuln.id)
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def test_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/v1/dashboard/summary")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_returns_the_callers_project_count(client: AsyncClient) -> None:
    _team, user, _project = await _seed(client)
    headers = _bearer_for(user)

    response = await client.get("/v1/dashboard/summary", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_count"] == 1
    assert body["scan_status_counts"]["succeeded"] == 1


# ---------------------------------------------------------------------------
# Cross-team isolation
# ---------------------------------------------------------------------------


async def test_a_member_never_sees_another_teams_projects_in_the_summary(
    client: AsyncClient,
) -> None:
    """Same isolation story as the other three dashboard routes.

    The route carries no path parameter: whatever the caller may see comes
    entirely from the accessible-projects scoping the service applies, so a
    caller in team A must see a project count of zero even though team B has
    a scanned, critically-vulnerable project.
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
    response = await client.get("/v1/dashboard/summary", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_count"] == 0
    assert body["vulnerability_severity_counts"]["critical"] == 0
    assert str(project_b.id) not in response.text


# ---------------------------------------------------------------------------
# Rate limiting (the W5 change: /summary used to have none)
# ---------------------------------------------------------------------------


async def test_summary_is_rate_limited_like_the_other_three_dashboard_routes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same bucket, same envelope as ``action-queue`` / ``trends`` / ``portfolio``.

    Set the cap to 2/min, fire three reads as the same actor, and assert the
    third trips 429 with the RFC 7807 envelope and ``Retry-After``, the exact
    shape ``test_scans_api.py::test_get_scan_is_rate_limited`` pins for the
    same ``api_read_rate_limit`` bucket on a different route.
    """
    monkeypatch.setenv("API_READ_RATE_LIMIT", "2/minute")

    _team, user, _project = await _seed(client)
    headers = _bearer_for(user)

    r1 = await client.get("/v1/dashboard/summary", headers=headers)
    r2 = await client.get("/v1/dashboard/summary", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    r3 = await client.get("/v1/dashboard/summary", headers=headers)
    assert r3.status_code == 429, r3.text
    assert r3.headers["content-type"].startswith(PROBLEM_JSON)
    assert r3.headers["Retry-After"] == "60"
    body = r3.json()
    assert body["status"] == 429
    assert body["title"] == "Too Many Requests"
    # RFC 7807 required fields, per the project-wide error contract (W5 DoD).
    assert body["type"]
    assert body["detail"]
    assert body["instance"]


# ---------------------------------------------------------------------------
# Truncation (ER9)
# ---------------------------------------------------------------------------
#
# The dashboard used to compute its KPIs and both distribution charts in the
# browser, from one page of `GET /v1/projects`, whose `size` caps at 100. Above
# 100 projects the numbers were not merely incomplete, they were biased: that
# list is ordered `updated_at DESC`, so what falls off the end is whatever
# nobody has touched recently, which is exactly where risk accumulates. A seed
# of 105 projects with every critical finding on the 5 oldest showed
# `critical: 0` on screen against a real 5.
#
# These tests pin the direction of that bias, not just the totals: they put the
# findings where the old truncation would have hidden them.


async def _seed_many_projects(
    client: AsyncClient,
    *,
    total: int,
    critical_on_oldest: int,
) -> tuple[User, int]:
    """Seed one team with `total` projects, findings on the oldest N only.

    Returns the actor and the number of projects carrying a critical finding.
    `updated_at` is written explicitly rather than left to the insert clock, so
    the "oldest" projects are unambiguously outside the first page of 100 no
    matter how fast the inserts run.
    """
    from datetime import UTC, datetime, timedelta

    factory = await _factory(client)
    base = datetime.now(UTC) - timedelta(days=total + 1)

    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")

        for index in range(total):
            project = await make_project(session, team=team)
            # Oldest first: index 0 is the furthest in the past, so it sorts
            # last under `updated_at DESC` and is the first to be truncated.
            project.updated_at = base + timedelta(minutes=index)
            session.add(project)
            scan = await make_scan(session, project=project, status="succeeded")
            if index < critical_on_oldest:
                await _critical_finding(session, scan_id=scan.id)
        await session.commit()

    return user, critical_on_oldest


async def test_summary_counts_every_project_past_the_list_page_ceiling(
    client: AsyncClient,
) -> None:
    """`project_count` is the real total, not the 100-row page it used to be."""
    user, _ = await _seed_many_projects(client, total=105, critical_on_oldest=5)

    response = await client.get("/v1/dashboard/summary", headers=_bearer_for(user))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_count"] == 105
    assert body["scan_status_counts"]["succeeded"] == 105


async def test_summary_sees_risk_in_the_projects_a_100_row_page_would_drop(
    client: AsyncClient,
) -> None:
    """The findings sit on the oldest projects, which the old page never loaded.

    This is the regression that matters. Counting off a truncated,
    recency-ordered page reported `critical: 0` here while five projects were
    actually critical, so the chart read cleanest exactly when it was most
    wrong.
    """
    user, expected_critical = await _seed_many_projects(client, total=105, critical_on_oldest=5)

    response = await client.get("/v1/dashboard/summary", headers=_bearer_for(user))

    assert response.status_code == 200, response.text
    body = response.json()

    # Project-shaped: five projects are in critical shape.
    assert body["project_severity_counts"]["critical"] == expected_critical
    # Component-shaped: one critical component in each of them.
    assert body["vulnerability_severity_counts"]["critical"] == expected_critical
    # And the other 100 are clean rather than unaccounted for.
    assert body["project_severity_counts"]["none"] == 105 - expected_critical


async def test_project_and_component_counts_answer_different_questions(
    client: AsyncClient,
) -> None:
    """Two critical CVEs on one project is 1 project, 2 components.

    The chart deep-links each segment to `/projects?severity=...`, so its
    counts have to be project-shaped; the KPI beside it is component-shaped.
    Conflating them is how a single noisy project came to dominate a portfolio
    view.
    """
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role="team_admin")
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        await _critical_finding(session, scan_id=scan.id)
        await _critical_finding(session, scan_id=scan.id)

    response = await client.get("/v1/dashboard/summary", headers=_bearer_for(user))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_severity_counts"]["critical"] == 1
    assert body["vulnerability_severity_counts"]["critical"] == 2

