"""
Integration tests for the dependency-graph endpoint — BomLens parity Phase H-1.

Covers ``GET /v1/projects/{id}/dependency-graph``: the resolved node/edge graph
of a project's latest (or pinned) succeeded scan.

Permission × state matrix (testing-guide hardening rule §1 — auth denial ALWAYS
before state): non-member → existence-hide 404, member → 200, super_admin bypass
→ 200, missing project → 404, another project's scan_id → 404. Plus a happy-path
shape assertion (nodes/edges/counts) and the node-cap truncation behaviour.

Runs against the real Postgres (CLAUDE.md core rule #1 — no SQLite). Mirrors the
seeding + client fixtures of ``test_project_diff_api.py``.
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import create_access_token
from models import (
    Component,
    ComponentDependencyEdge,
    ComponentVersion,
    Scan,
    ScanComponent,
    Team,
    User,
    Vulnerability,
    VulnerabilityFinding,
)
from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
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
        pytest.skip("DATABASE_URL not set — skip dependency-graph API tests")
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
            f"alembic upgrade head failed; dependency-graph API tests cannot run\n"
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
# Seeding helpers
# ---------------------------------------------------------------------------


async def _make_scan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    status: str = "succeeded",
    created_at: datetime | None = None,
) -> Scan:
    scan = Scan(
        project_id=project_id,
        kind="source",
        status=status,
        progress_percent=100 if status == "succeeded" else 0,
        scan_metadata={},
        created_at=created_at or datetime(2026, 6, 1, tzinfo=UTC),
    )
    session.add(scan)
    await session.commit()
    await session.refresh(scan)
    return scan


async def _make_cv(
    session: AsyncSession, *, name: str, version: str = "1.0.0"
) -> ComponentVersion:
    purl = f"pkg:npm/{name}"
    component = Component(purl=purl, package_type="npm", name=name)
    session.add(component)
    await session.commit()
    await session.refresh(component)
    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=f"{purl}@{version}",
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return cv


async def _attach(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    direct: bool,
    depth: int | None,
) -> None:
    session.add(
        ScanComponent(
            scan_id=scan_id,
            component_version_id=cv_id,
            direct=direct,
            depth=depth,
            raw_data={},
        )
    )
    await session.commit()


async def _edge(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    parent_cv_id: uuid.UUID,
    child_cv_id: uuid.UUID,
) -> None:
    session.add(
        ComponentDependencyEdge(
            scan_id=scan_id,
            parent_component_version_id=parent_cv_id,
            child_component_version_id=child_cv_id,
        )
    )
    await session.commit()


async def _finding_with_vuln(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    severity: str,
) -> None:
    suffix = unique_suffix()
    v = Vulnerability(
        external_id=f"CVE-2024-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"vuln {suffix}",
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    session.add(
        VulnerabilityFinding(
            scan_id=scan_id, component_version_id=cv_id, vulnerability_id=v.id
        )
    )
    await session.commit()


async def _seed_team_with_user(client: AsyncClient, *, role: str = "developer"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
        await make_membership(session, user=user, team=team, role=role)
    return team, user


async def _seed_project(client: AsyncClient, *, team_id: uuid.UUID) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        project = await make_project(session, team=team)
        return project.id


async def _seed_small_graph(client: AsyncClient, *, project_id: uuid.UUID) -> uuid.UUID:
    """A→B (B has a high CVE) + orphan C. Returns the scan id."""
    factory = await _factory(client)
    async with factory() as session:
        scan = await _make_scan(session, project_id=project_id)
        suffix = unique_suffix()
        a = await _make_cv(session, name=f"app-{suffix}")
        b = await _make_cv(session, name=f"beta-{suffix}")
        c = await _make_cv(session, name=f"orphan-{suffix}")
        await _attach(session, scan_id=scan.id, cv_id=a.id, direct=True, depth=1)
        await _attach(session, scan_id=scan.id, cv_id=b.id, direct=False, depth=2)
        await _attach(session, scan_id=scan.id, cv_id=c.id, direct=False, depth=None)
        await _edge(session, scan_id=scan.id, parent_cv_id=a.id, child_cv_id=b.id)
        await _finding_with_vuln(
            session, scan_id=scan.id, cv_id=b.id, severity="high"
        )
        return scan.id


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_graph_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/dependency-graph")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path — member gets the full graph shape
# ---------------------------------------------------------------------------


async def test_graph_member_gets_nodes_and_edges(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_small_graph(client, project_id=project_id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/dependency-graph", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()

    assert body["scan_id"] == str(scan_id)
    assert body["node_count"] == 3
    assert body["edge_count"] == 1
    assert body["node_cap"] == 5000
    assert body["truncated"] is False
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) == 1

    # The high-CVE node reports its severity + count.
    worst = max(body["nodes"], key=lambda n: n["vulnerability_count"])
    assert worst["vulnerability_count"] == 1
    assert worst["max_severity"] == "high"

    # Edge endpoints reference real node ids.
    node_ids = {n["id"] for n in body["nodes"]}
    edge = body["edges"][0]
    assert edge["source"] in node_ids
    assert edge["target"] in node_ids


# ---------------------------------------------------------------------------
# RBAC — permission × state matrix (auth denial before state)
# ---------------------------------------------------------------------------


async def test_graph_non_member_is_existence_hidden_404(client) -> None:
    owner_team, _owner = await _seed_team_with_user(client)
    _, outsider = await _seed_team_with_user(client)
    project_id = await _seed_project(client, team_id=owner_team.id)
    await _seed_small_graph(client, project_id=project_id)
    headers = _bearer_for(outsider)

    response = await client.get(
        f"/v1/projects/{project_id}/dependency-graph", headers=headers
    )
    # Non-member is existence-hidden as 404 (not 403): the endpoint contract
    # hides the project's existence from a cross-team caller.
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_graph_super_admin_bypasses_membership(client) -> None:
    owner_team, _owner = await _seed_team_with_user(client)
    project_id = await _seed_project(client, team_id=owner_team.id)
    await _seed_small_graph(client, project_id=project_id)

    factory = await _factory(client)
    async with factory() as session:
        admin = await make_user(session, is_superuser=True)
    headers = _bearer_for(admin)

    response = await client.get(
        f"/v1/projects/{project_id}/dependency-graph", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["node_count"] == 3


async def test_graph_missing_project_is_404(client) -> None:
    _team, user = await _seed_team_with_user(client)
    headers = _bearer_for(user)
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/dependency-graph", headers=headers
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_graph_cross_project_scan_id_is_404(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    await _seed_small_graph(client, project_id=project_id)
    headers = _bearer_for(user)

    # A succeeded scan of ANOTHER project must not be pinnable here → 404.
    other_team, _ = await _seed_team_with_user(client)
    other_project_id = await _seed_project(client, team_id=other_team.id)
    foreign_scan_id = await _seed_small_graph(client, project_id=other_project_id)

    response = await client.get(
        f"/v1/projects/{project_id}/dependency-graph",
        headers=headers,
        params={"scan_id": str(foreign_scan_id)},
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_graph_pinned_scan_id_resolves(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_project(client, team_id=team.id)
    scan_id = await _seed_small_graph(client, project_id=project_id)
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/dependency-graph",
        headers=headers,
        params={"scan_id": str(scan_id)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scan_id"] == str(scan_id)
