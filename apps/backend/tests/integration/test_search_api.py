"""
Integration tests for the global cross-project search API — BomLens parity H-2.

Endpoint:
  - GET /v1/search?q=<str>&kinds=<csv>

The dominant concern is TEAM ISOLATION: this endpoint fans out across every
project the caller can read, so a cross-team leak is a P0. The headline test
(`test_team_isolation_no_cross_leak`) seeds matching components AND CVEs in two
different teams' projects and asserts, at the intersection, that team A's search
never returns team B's rows and vice versa, while a super-admin sees both.

Because the integration DB is not truncated between tests and a super-admin
sees ALL projects, every test embeds a per-test unique token in the seeded
names / CVE ids and searches for THAT token — so tests never contaminate each
other's assertions.

Wire format (RFC 7807 on 401), the auth gate, min-length, the 20-row cap, LIKE
escaping, and the `kinds` filter are pinned here.
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
        pytest.skip("DATABASE_URL not set — skip search API tests")
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
            "alembic upgrade head failed; search API tests cannot run\n"
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


def _token() -> str:
    """Short unique token embedded in seeded names + searched for."""
    return "tok" + uuid.uuid4().hex[:10]


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

        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        project = await make_project(session, team=team)
        scan = await make_scan(session, project=project, status="succeeded")
        project.latest_scan_id = scan.id
        project.updated_at = datetime.now(tz=UTC)
        await session.commit()
        await session.refresh(project)
        return project.id, scan.id


async def _seed_component(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    name: str,
    version: str = "1.0.0",
    purl: str | None = None,
) -> None:
    """Insert Component + ComponentVersion + ScanComponent tied to scan_id."""
    factory = await _factory(client)
    async with factory() as session:
        from models import Component, ComponentVersion, ScanComponent

        suffix = uuid.uuid4().hex[:8]
        resolved_purl = purl or f"pkg:npm/{name}-{suffix}"
        component = Component(purl=resolved_purl, package_type="npm", name=name)
        session.add(component)
        await session.commit()
        await session.refresh(component)

        cv = ComponentVersion(
            component_id=component.id,
            version=version,
            purl_with_version=f"{resolved_purl}@{version}-{suffix}",
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)

        session.add(ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True))
        await session.commit()


async def _seed_vuln(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    cve_id: str,
    severity: str = "high",
) -> None:
    """Insert Component + CV + Vulnerability + VulnerabilityFinding for scan_id."""
    factory = await _factory(client)
    async with factory() as session:
        from models import (
            Component,
            ComponentVersion,
            Vulnerability,
            VulnerabilityFinding,
        )

        suffix = uuid.uuid4().hex[:8]
        purl = f"pkg:npm/vulnpkg-{suffix}"
        component = Component(purl=purl, package_type="npm", name=f"vulnpkg-{suffix}")
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

        vuln = Vulnerability(
            external_id=cve_id,
            source="NVD",
            severity=severity,
            summary=f"summary {suffix}",
        )
        session.add(vuln)
        await session.commit()
        await session.refresh(vuln)

        session.add(
            VulnerabilityFinding(
                scan_id=scan_id,
                component_version_id=cv.id,
                vulnerability_id=vuln.id,
                status="new",
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_search_without_auth_returns_401(client) -> None:
    response = await client.get("/v1/search", params={"q": "anything"})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Team isolation — the P0 test. Cross-leak must be exactly zero.
# ---------------------------------------------------------------------------


async def test_team_isolation_no_cross_leak(client) -> None:
    token = _token()

    _, team_a, user_a = await _seed_team_with_user(client)
    _, team_b, user_b = await _seed_team_with_user(client)

    proj_a, scan_a = await _seed_scanned_project(client, team_id=team_a.id)
    proj_b, scan_b = await _seed_scanned_project(client, team_id=team_b.id)

    # Matching component + CVE in BOTH teams' projects, both carrying the token.
    await _seed_component(client, scan_id=scan_a, name=f"{token}-comp-a")
    await _seed_component(client, scan_id=scan_b, name=f"{token}-comp-b")
    cve_a = f"CVE-2099-{token.upper()}-A"
    cve_b = f"CVE-2099-{token.upper()}-B"
    await _seed_vuln(client, scan_id=scan_a, cve_id=cve_a)
    await _seed_vuln(client, scan_id=scan_b, cve_id=cve_b)

    # --- Actor A: sees ONLY team A's rows, at the intersection ---
    resp_a = await client.get("/v1/search", headers=_bearer_for(user_a), params={"q": token})
    assert resp_a.status_code == 200, resp_a.text
    body_a = resp_a.json()

    comp_projects_a = {c["project_id"] for c in body_a["components"]}
    vuln_projects_a = {v["project_id"] for v in body_a["vulnerabilities"]}
    cves_a = {v["cve_id"] for v in body_a["vulnerabilities"]}
    # A's own project is present …
    assert str(proj_a) in comp_projects_a
    assert cve_a in cves_a
    # … and team B leaks NOTHING (zero cross-leak — the explicit intersection).
    assert str(proj_b) not in comp_projects_a
    assert str(proj_b) not in vuln_projects_a
    assert cve_b not in cves_a

    # --- Actor B: the mirror. Sees ONLY team B's rows ---
    resp_b = await client.get("/v1/search", headers=_bearer_for(user_b), params={"q": token})
    assert resp_b.status_code == 200, resp_b.text
    body_b = resp_b.json()

    comp_projects_b = {c["project_id"] for c in body_b["components"]}
    vuln_projects_b = {v["project_id"] for v in body_b["vulnerabilities"]}
    cves_b = {v["cve_id"] for v in body_b["vulnerabilities"]}
    assert str(proj_b) in comp_projects_b
    assert cve_b in cves_b
    assert str(proj_a) not in comp_projects_b
    assert str(proj_a) not in vuln_projects_b
    assert cve_a not in cves_b


async def test_super_admin_sees_all_teams(client) -> None:
    token = _token()

    _, team_a, _ = await _seed_team_with_user(client)
    _, team_b, _ = await _seed_team_with_user(client)
    _, _, admin = await _seed_team_with_user(client, is_superuser=True)

    proj_a, scan_a = await _seed_scanned_project(client, team_id=team_a.id)
    proj_b, scan_b = await _seed_scanned_project(client, team_id=team_b.id)
    await _seed_component(client, scan_id=scan_a, name=f"{token}-comp-a")
    await _seed_component(client, scan_id=scan_b, name=f"{token}-comp-b")

    resp = await client.get("/v1/search", headers=_bearer_for(admin), params={"q": token})
    assert resp.status_code == 200, resp.text
    projects = {c["project_id"] for c in resp.json()["components"]}
    assert {str(proj_a), str(proj_b)} <= projects


# ---------------------------------------------------------------------------
# Query length / cap / escaping / kinds
# ---------------------------------------------------------------------------


async def test_query_too_short_returns_empty_200(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_scanned_project(client, team_id=team.id)
    await _seed_component(client, scan_id=scan, name="a")

    resp = await client.get("/v1/search", headers=_bearer_for(user), params={"q": "a"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"query": "a", "components": [], "vulnerabilities": []}


async def test_component_cap_is_20(client) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_scanned_project(client, team_id=team.id)
    for i in range(25):
        await _seed_component(client, scan_id=scan, name=f"{token}-pkg-{i:02d}")

    resp = await client.get("/v1/search", headers=_bearer_for(user), params={"q": token})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["components"]) == 20


async def test_like_wildcard_is_escaped(client) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_scanned_project(client, team_id=team.id)
    # Literal '%' in the name; a sibling replaces '%' with a letter.
    literal_name = f"{token}-spec%ial"
    decoy_name = f"{token}-specXial"
    await _seed_component(client, scan_id=scan, name=literal_name)
    await _seed_component(client, scan_id=scan, name=decoy_name)

    # Search for the literal '%' — it must match ONLY the literal name, not act
    # as a wildcard that also sweeps in the decoy.
    resp = await client.get(
        "/v1/search", headers=_bearer_for(user), params={"q": f"{token}-spec%ial"}
    )
    assert resp.status_code == 200, resp.text
    names = {c["component_name"] for c in resp.json()["components"]}
    assert names == {literal_name}


async def test_kinds_filter_components_only(client) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_scanned_project(client, team_id=team.id)
    await _seed_component(client, scan_id=scan, name=f"{token}-comp")
    await _seed_vuln(client, scan_id=scan, cve_id=f"CVE-2099-{token.upper()}")

    resp = await client.get(
        "/v1/search",
        headers=_bearer_for(user),
        params={"q": token, "kinds": "components"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["components"]) == 1
    assert body["vulnerabilities"] == []


async def test_kinds_filter_vulnerabilities_only(client) -> None:
    token = _token()
    _, team, user = await _seed_team_with_user(client)
    _, scan = await _seed_scanned_project(client, team_id=team.id)
    await _seed_component(client, scan_id=scan, name=f"{token}-comp")
    await _seed_vuln(client, scan_id=scan, cve_id=f"CVE-2099-{token.upper()}")

    resp = await client.get(
        "/v1/search",
        headers=_bearer_for(user),
        params={"q": token, "kinds": "vulnerabilities"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["components"] == []
    assert len(body["vulnerabilities"]) == 1
