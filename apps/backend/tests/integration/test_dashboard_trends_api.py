"""``GET /v1/dashboard/trends`` — HTTP contract, window validation, isolation.

Like the action queue, this route has no path parameter: there is no resource
to check access against, so whatever the caller can see is decided entirely by
the scoping the service applies. That makes the isolation case here the whole
security story for the route rather than a supplement to a per-resource check,
which is why it is asserted at the HTTP boundary and not only at the service.

The ``days`` parameter gets its own cases because it is the only input a caller
controls, and the thing it controls is how much history the query walks.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration

PASSWORD = "correct-horse-battery-staple"


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip dashboard trends API tests")
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
async def db_session() -> AsyncIterator[AsyncSession]:
    from core.audit import install_audit_listeners
    from core.config import database_url

    engine = create_async_engine(database_url(), pool_pre_ping=True, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    install_audit_listeners(factory)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    from main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _user_with_password(session: AsyncSession):
    """A user we can actually sign in as — these cases go through real auth."""
    from core.security import hash_password
    from models import User

    suffix = unique_suffix()
    user = User(
        email=f"trends-{suffix}@example.com",
        full_name=f"Trends {suffix}",
        hashed_password=hash_password(PASSWORD),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _login(client: httpx.AsyncClient, email: str) -> str:
    response = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


async def _critical_finding(session: AsyncSession, *, scan_id: uuid.UUID) -> None:
    from models import (
        Component,
        ComponentVersion,
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

    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix}",
        source="NVD",
        severity="critical",
        summary="api fixture",
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


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/dashboard/trends")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_defaults_to_a_thirty_day_window(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    user = await _user_with_password(db_session)
    token = await _login(client, user.email)

    response = await client.get(
        "/v1/dashboard/trends", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["period_days"] == 30
    assert len(body["points"]) == 30
    assert body["project_count"] == 0
    assert body["totals"] == {"new_findings": 0, "resolved_findings": 0}


@pytest.mark.parametrize("days", [7, 30, 90])
async def test_each_offered_window_returns_that_many_points(
    client: httpx.AsyncClient, db_session: AsyncSession, days: int
) -> None:
    user = await _user_with_password(db_session)
    token = await _login(client, user.email)

    response = await client.get(
        "/v1/dashboard/trends",
        params={"days": days},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["period_days"] == days
    assert len(body["points"]) == days
    assert body["points"][-1]["date"] == body["end_date"]
    assert body["points"][0]["date"] == body["start_date"]


@pytest.mark.parametrize("days", ["365", "0", "-7", "31", "abc"])
async def test_a_window_outside_the_offered_set_is_refused(
    client: httpx.AsyncClient, db_session: AsyncSession, days: str
) -> None:
    """The only caller-controlled input decides how much history is walked.

    A free-form integer would let one request scan years of scans, so the
    parameter is a closed set at the route and re-checked in the service.
    """
    user = await _user_with_password(db_session)
    token = await _login(client, user.email)

    response = await client.get(
        "/v1/dashboard/trends",
        params={"days": days},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_a_member_sees_their_own_team_and_only_it(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """The isolation assertion that matters most for a route with no path id.

    Both teams carry a project, and team B's carries three findings to team
    A's one, so the exact number proves the boundary in a single execution.

    The first version of this test gave team A no project at all. That made
    the caller's accessible-project list empty, the service returned its
    all-zero series before running a single query, and deleting every
    ``project_id`` filter in the module would have left it green — it asserted
    the early return, not the isolation. A test that reaches only the states
    where two paths cannot differ proves nothing about whether they do.
    """
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    yesterday = datetime.now(tz=UTC) - timedelta(days=1)

    project_a = await make_project(db_session, team=team_a)
    scan_a = await make_scan(
        db_session, project=project_a, status="succeeded", created_at=yesterday
    )
    await _critical_finding(db_session, scan_id=scan_a.id)

    project_b = await make_project(db_session, team=team_b)
    scan_b = await make_scan(
        db_session, project=project_b, status="succeeded", created_at=yesterday
    )
    for _ in range(3):
        await _critical_finding(db_session, scan_id=scan_b.id)

    member_a = await _user_with_password(db_session)
    await make_membership(db_session, user=member_a, team=team_a, role="developer")
    token = await _login(client, member_a.email)

    response = await client.get(
        "/v1/dashboard/trends",
        params={"days": 7},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["project_count"] == 1
    assert body["points"][-1]["critical_open"] == 1, (
        "a team-A member's series carried team-B exposures"
    )
    assert sum(point["scan_count"] for point in body["points"]) == 1


async def test_a_member_sees_their_own_project(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    scan = await make_scan(
        db_session,
        project=project,
        status="succeeded",
        created_at=datetime.now(tz=UTC) - timedelta(days=1),
    )
    await _critical_finding(db_session, scan_id=scan.id)

    member = await _user_with_password(db_session)
    await make_membership(db_session, user=member, team=team, role="developer")
    token = await _login(client, member.email)

    response = await client.get(
        "/v1/dashboard/trends",
        params={"days": 7},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["project_count"] == 1
    assert body["points"][-1]["critical_open"] == 1
    # No flow: one scan has no predecessor to have changed relative to.
    assert body["totals"] == {"new_findings": 0, "resolved_findings": 0}
