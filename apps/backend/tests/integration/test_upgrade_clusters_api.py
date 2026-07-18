"""
Integration tests for the "Group by upgrade" surface — W9-#53.

Endpoint:
  - GET /v1/projects/{project_id}/vulnerabilities/upgrade-clusters

Pins the clustering contract (one cluster per component, semver-max recommended
version, finding_count == open findings, dispositioned findings excluded), the
priority sort (direct/critical before indirect/low), and the auth / existence
gate (permission + existence asserted BEFORE state — hardening rule #1).
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
        pytest.skip("DATABASE_URL not set — skip upgrade-clusters API tests")
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
            "alembic upgrade head failed; upgrade-clusters API tests cannot run\n"
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


async def _seed_team_with_user(client: AsyncClient, *, role: str = "developer"):
    factory = await _factory(client)
    async with factory() as session:
        org = await make_organization(session)
        team = await make_team(session, organization=org)
        user = await make_user(session)
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


async def _seed_component_cluster(
    client: AsyncClient,
    *,
    scan_id: uuid.UUID,
    name: str,
    current_version: str = "1.0.0",
    direct: bool = False,
    depth: int | None = None,
    findings: list[dict[str, object]],
) -> uuid.UUID:
    """Insert one component_version + a ScanComponent row + N findings.

    Each entry in ``findings`` is ``{severity, fixed_version, status, epss, kev,
    cve_id?}`` and becomes one Vulnerability + VulnerabilityFinding on the shared
    component_version. Returns the component_version id.
    """
    factory = await _factory(client)
    async with factory() as session:
        from models import (
            Component,
            ComponentVersion,
            ScanComponent,
            Vulnerability,
            VulnerabilityFinding,
        )

        suffix = uuid.uuid4().hex[:10]
        # ``components.purl`` is globally unique — keep the readable name but make
        # the purl unique so tests can reuse friendly names across runs.
        purl = f"pkg:npm/{name}-{suffix}"
        component = Component(purl=purl, package_type="npm", name=name)
        session.add(component)
        await session.commit()
        await session.refresh(component)

        cv = ComponentVersion(
            component_id=component.id,
            version=current_version,
            purl_with_version=f"{purl}@{current_version}-{suffix}",
        )
        session.add(cv)
        await session.commit()
        await session.refresh(cv)

        session.add(
            ScanComponent(
                scan_id=scan_id,
                component_version_id=cv.id,
                direct=direct,
                depth=depth,
            )
        )

        for i, spec in enumerate(findings):
            # ``vulnerabilities.external_id`` is globally unique — suffix the
            # logical id so friendly names can repeat across tests / reruns.
            vuln = Vulnerability(
                external_id=f"{spec.get('cve_id') or 'CVE'}-{suffix}-{i}",
                source="NVD",
                severity=spec.get("severity", "high"),
                summary=f"summary {suffix} {i}",
                epss_score=spec.get("epss"),
                kev=bool(spec.get("kev", False)),
            )
            session.add(vuln)
            await session.commit()
            await session.refresh(vuln)

            session.add(
                VulnerabilityFinding(
                    scan_id=scan_id,
                    component_version_id=cv.id,
                    vulnerability_id=vuln.id,
                    status=spec.get("status", "new"),
                    fixed_version=spec.get("fixed_version"),
                )
            )
        await session.commit()
        return cv.id


def _url(project_id: uuid.UUID) -> str:
    return f"/v1/projects/{project_id}/vulnerabilities/upgrade-clusters"


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_upgrade_clusters_without_auth_returns_401(client) -> None:
    response = await client.get(_url(uuid.uuid4()))
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Clustering behaviour
# ---------------------------------------------------------------------------


async def test_two_findings_collapse_into_one_cluster_at_semver_max(client) -> None:
    """Two open findings with fix versions 4.17.20 / 4.17.21 → ONE cluster,
    recommended_version == the semver max, finding_count == 2."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    await _seed_component_cluster(
        client,
        scan_id=scan_id,
        name="lodash",
        current_version="4.17.19",
        direct=True,
        depth=1,
        findings=[
            {"severity": "high", "fixed_version": "4.17.20", "cve_id": "CVE-2099-A"},
            {"severity": "critical", "fixed_version": "4.17.21", "cve_id": "CVE-2099-B"},
        ],
    )

    response = await client.get(_url(project_id), headers=_bearer_for(user))
    assert response.status_code == 200
    body = response.json()
    assert body["total_findings"] == 2
    assert len(body["clusters"]) == 1
    cluster = body["clusters"][0]
    assert cluster["component_name"] == "lodash"
    assert cluster["recommended_version"] == "4.17.21"
    assert cluster["reason"] == "ok"
    assert cluster["finding_count"] == 2
    assert cluster["max_severity"] == "critical"
    assert cluster["direct"] is True
    # Findings sorted worst-first: critical before high.
    assert [f["severity"] for f in cluster["findings"]] == ["critical", "high"]


async def test_missing_fix_version_yields_no_fix_reason(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    await _seed_component_cluster(
        client,
        scan_id=scan_id,
        name="left-pad",
        findings=[{"severity": "high", "fixed_version": None, "cve_id": "CVE-2099-NF"}],
    )

    response = await client.get(_url(project_id), headers=_bearer_for(user))
    assert response.status_code == 200
    body = response.json()
    assert body["total_findings"] == 1
    assert len(body["clusters"]) == 1
    cluster = body["clusters"][0]
    assert cluster["reason"] == "no_fix_version"
    assert cluster["recommended_version"] is None
    assert cluster["finding_count"] == 1


async def test_dispositioned_findings_excluded_and_counts_add_up(client) -> None:
    """total_findings equals the OPEN findings; not_affected/fixed/false_positive
    are excluded; suppressed stays (still open work)."""
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    # Component A: 1 open + 1 not_affected (disposed) → 1 open finding.
    await _seed_component_cluster(
        client,
        scan_id=scan_id,
        name="pkg-a",
        findings=[
            {"severity": "high", "fixed_version": "2.0.0", "status": "new", "cve_id": "CVE-A-OPEN"},
            {
                "severity": "critical",
                "fixed_version": "3.0.0",
                "status": "not_affected",
                "cve_id": "CVE-A-NA",
            },
        ],
    )
    # Component B: 1 suppressed (still open) + 1 fixed (disposed) → 1 open finding.
    await _seed_component_cluster(
        client,
        scan_id=scan_id,
        name="pkg-b",
        findings=[
            {
                "severity": "medium",
                "fixed_version": "1.2.0",
                "status": "suppressed",
                "cve_id": "CVE-B-SUP",
            },
            {
                "severity": "low",
                "fixed_version": "1.3.0",
                "status": "fixed",
                "cve_id": "CVE-B-FIXED",
            },
        ],
    )

    response = await client.get(_url(project_id), headers=_bearer_for(user))
    assert response.status_code == 200
    body = response.json()
    # 2 open findings total (one per component), both dispositioned ones dropped.
    assert body["total_findings"] == 2
    by_name = {c["component_name"]: c for c in body["clusters"]}
    assert by_name["pkg-a"]["finding_count"] == 1
    assert by_name["pkg-a"]["recommended_version"] == "2.0.0"
    assert by_name["pkg-b"]["finding_count"] == 1
    # The surviving finding is the suppressed medium (still open), not the fixed low.
    assert [f["status"] for f in by_name["pkg-b"]["findings"]] == ["suppressed"]
    assert by_name["pkg-b"]["recommended_version"] == "1.2.0"


async def test_priority_sort_direct_critical_before_indirect_low(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, scan_id = await _seed_scanned_project(client, team_id=team.id)
    # Indirect low.
    await _seed_component_cluster(
        client,
        scan_id=scan_id,
        name="aaa-indirect-low",
        direct=False,
        depth=3,
        findings=[{"severity": "low", "fixed_version": "1.1.0", "cve_id": "CVE-LOW"}],
    )
    # Direct critical.
    await _seed_component_cluster(
        client,
        scan_id=scan_id,
        name="zzz-direct-critical",
        direct=True,
        depth=1,
        findings=[{"severity": "critical", "fixed_version": "2.2.0", "cve_id": "CVE-CRIT"}],
    )

    response = await client.get(_url(project_id), headers=_bearer_for(user))
    assert response.status_code == 200
    names = [c["component_name"] for c in response.json()["clusters"]]
    # Direct critical sorts first despite its name sorting last alphabetically.
    assert names[0] == "zzz-direct-critical"
    assert names[1] == "aaa-indirect-low"


# ---------------------------------------------------------------------------
# Auth / existence before state (hardening rule #1)
# ---------------------------------------------------------------------------


async def test_non_member_gets_403(client) -> None:
    _, team, _ = await _seed_team_with_user(client)
    project_id, _scan = await _seed_scanned_project(client, team_id=team.id)
    _, _other_team, outsider = await _seed_team_with_user(client)

    response = await client.get(_url(project_id), headers=_bearer_for(outsider))
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_nonexistent_project_returns_404(client) -> None:
    _, _team, user = await _seed_team_with_user(client)
    response = await client.get(_url(uuid.uuid4()), headers=_bearer_for(user))
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_bad_scan_id_from_other_project_returns_404(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    project_id, _scan = await _seed_scanned_project(client, team_id=team.id)
    # A succeeded scan that belongs to a DIFFERENT project.
    _other_project, other_scan = await _seed_scanned_project(client, team_id=team.id)

    response = await client.get(
        _url(project_id),
        params={"scan_id": str(other_scan)},
        headers=_bearer_for(user),
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_project_without_succeeded_scan_returns_empty_200(client) -> None:
    _, team, user = await _seed_team_with_user(client)
    factory = await _factory(client)
    async with factory() as session:
        from sqlalchemy import select

        from models import Team

        team_obj = (await session.execute(select(Team).where(Team.id == team.id))).scalar_one()
        project = await make_project(session, team=team_obj)
        # Only a failed scan — no succeeded snapshot to resolve.
        await make_scan(session, project=project, status="failed")
        await session.commit()
        project_id = project.id

    response = await client.get(_url(project_id), headers=_bearer_for(user))
    assert response.status_code == 200
    body = response.json()
    assert body["scan_id"] is None
    assert body["total_findings"] == 0
    assert body["clusters"] == []
