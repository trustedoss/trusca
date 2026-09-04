"""``GET /v1/dashboard/action-queue`` — HTTP contract and isolation.

The endpoint has no path parameter, so there is no resource to check access
against: whatever the caller can see is decided entirely by the scoping the
service applies. That makes the isolation assertions here the whole security
story for this route, not a supplement to a per-resource check — which is why
they are asserted at the HTTP boundary and not only at the service layer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._db_required import migrate_to_head
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    unique_suffix,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


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


async def _user_with_password(session: AsyncSession, password: str):
    """A user we can actually sign in as.

    ``make_user`` hashes a random password, which is fine for service-layer
    tests but leaves nothing to POST to /auth/login. These cases go through
    the real HTTP auth path on purpose — the isolation this route relies on
    is applied to the principal the token resolves to.
    """
    from core.security import hash_password
    from models import User

    suffix = unique_suffix()
    user = User(
        email=f"queue-{suffix}@example.com",
        full_name=f"Queue {suffix}",
        hashed_password=hash_password(password),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def _login(client: httpx.AsyncClient, email: str, password: str) -> str:
    response = await client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    assert isinstance(token, str)
    return token


async def _critical_finding(session: AsyncSession, *, scan_id: uuid.UUID) -> None:
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
        external_id=f"CVE-2024-{suffix}",
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
    response = await client.get("/v1/dashboard/action-queue")

    assert response.status_code == 401
    # RFC 7807 for every 4xx, per the project-wide error contract.
    assert response.headers["content-type"].startswith("application/problem+json")


async def test_returns_empty_buckets_for_a_user_with_no_projects(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    password = "correct-horse-battery-staple"
    user = await _user_with_password(db_session, password)

    token = await _login(client, user.email, password)
    response = await client.get(
        "/v1/dashboard/action-queue", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["pending_approvals"] == 0
    assert body["kev_sla"] == {"overdue": 0, "due_soon": 0}
    assert body["gate_blocked"] == []
    assert body["stale_projects"] == []


async def test_a_member_never_sees_another_team_through_this_route(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """The isolation assertion that matters most for a route with no path id."""
    password = "correct-horse-battery-staple"
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)

    # A blocked project in team B.
    project_b = await make_project(db_session, team=team_b)
    scan_b = await make_scan(db_session, project=project_b, status="succeeded")
    await _critical_finding(db_session, scan_id=scan_b.id)

    member_a = await _user_with_password(db_session, password)
    await make_membership(db_session, user=member_a, team=team_a, role="developer")

    token = await _login(client, member_a.email, password)
    response = await client.get(
        "/v1/dashboard/action-queue", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["gate_blocked"] == [], (
        "a team-A member saw a team-B project in the action queue"
    )
    assert all(
        entry["project_id"] != str(project_b.id) for entry in body["stale_projects"]
    )


async def test_a_member_sees_their_own_blocked_project(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """The mirror of the isolation case — scoping must not over-filter either.

    Asserted alongside it deliberately: a scoping bug that returns nothing at
    all passes an isolation test on its own.
    """
    password = "correct-horse-battery-staple"
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)

    project = await make_project(db_session, team=team)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _critical_finding(db_session, scan_id=scan.id)

    member = await _user_with_password(db_session, password)
    await make_membership(db_session, user=member, team=team, role="developer")

    token = await _login(client, member.email, password)
    response = await client.get(
        "/v1/dashboard/action-queue", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    blocked = response.json()["gate_blocked"]
    assert [entry["project_id"] for entry in blocked] == [str(project.id)]
    assert blocked[0]["critical_cve_count"] == 1
    assert blocked[0]["scan_id"] == str(scan.id)
