"""
Integration tests for release diff — feature #28 Phase 2.

Covers ``GET /v1/projects/{id}/diff?base=<scan_id>&target=<scan_id>``: the diff
between two SUCCEEDED-scan snapshots of the same project.

Scenarios:
  - log4j removed + its CVE resolved + a new component (with a new CVE) added
    between base and target → added / removed / introduced / resolved + summary
    deltas are all correct.
  - a version bump of the SAME package shows up in components.changed (with
    base/target versions) and NOT in added/removed.
  - base == target → all change-set lists empty, summaries equal.
  - IDOR / invalid-pin guards: a base/target from another project → 404; a
    non-succeeded scan id → 404; a missing required query param → 422.

These run against the real Postgres (CLAUDE.md core rule #1 — no SQLite). Each
test seeds its own org → team → user → project graph with two SUCCEEDED scans
carrying DIFFERENT components / CVE findings keyed by scan_id, so the diff has
something to compute.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import create_access_token
from models import (
    Component,
    ComponentVersion,
    License,
    LicenseFinding,
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
        pytest.skip("DATABASE_URL not set — skip project-diff API tests")
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
            f"alembic upgrade head failed; project-diff API tests cannot run\n"
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
# Seeding helpers — build a scan and attach explicit named packages + CVEs so the
# diff has a controllable, assertable shape.
# ---------------------------------------------------------------------------


async def _get_or_make_component(
    session: AsyncSession,
    *,
    purl: str,
    name: str,
    namespace: str | None = None,
) -> Component:
    existing = await session.scalar(select(Component).where(Component.purl == purl))
    if existing is not None:
        return existing
    component = Component(
        purl=purl,
        package_type="generic",
        name=name,
        namespace=namespace,
    )
    session.add(component)
    await session.commit()
    await session.refresh(component)
    return component


async def _get_or_make_cv(
    session: AsyncSession,
    *,
    component: Component,
    version: str,
) -> ComponentVersion:
    purl_with_version = f"{component.purl}@{version}"
    existing = await session.scalar(
        select(ComponentVersion).where(
            ComponentVersion.purl_with_version == purl_with_version
        )
    )
    if existing is not None:
        return existing
    cv = ComponentVersion(
        component_id=component.id,
        version=version,
        purl_with_version=purl_with_version,
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)
    return cv


async def _get_or_make_vuln(
    session: AsyncSession,
    *,
    external_id: str,
    severity: str,
) -> Vulnerability:
    existing = await session.scalar(
        select(Vulnerability).where(Vulnerability.external_id == external_id)
    )
    if existing is not None:
        return existing
    v = Vulnerability(
        external_id=external_id,
        source="NVD",
        severity=severity,
        summary=f"vuln {external_id}",
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _get_or_make_license(
    session: AsyncSession, *, spdx_id: str, category: str
) -> License:
    existing = await session.scalar(select(License).where(License.spdx_id == spdx_id))
    if existing is not None:
        return existing
    lic = License(spdx_id=spdx_id, name=spdx_id, category=category)
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    return lic


async def _make_empty_scan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    created_at: datetime,
    release: str | None = None,
    status: str = "succeeded",
) -> Scan:
    metadata: dict[str, str] = {}
    if release is not None:
        metadata["release"] = release
    scan = Scan(
        project_id=project_id,
        kind="source",
        status=status,
        progress_percent=100 if status == "succeeded" else 0,
        scan_metadata=metadata,
        created_at=created_at,
    )
    session.add(scan)
    await session.commit()
    await session.refresh(scan)
    return scan


async def _attach_component(
    session: AsyncSession,
    *,
    scan: Scan,
    cv: ComponentVersion,
) -> None:
    session.add(
        ScanComponent(
            scan_id=scan.id,
            component_version_id=cv.id,
            direct=True,
            depth=1,
            raw_data={},
        )
    )
    await session.commit()


async def _attach_finding(
    session: AsyncSession,
    *,
    scan: Scan,
    cv: ComponentVersion,
    vuln: Vulnerability,
    status: str = "new",
) -> None:
    session.add(
        VulnerabilityFinding(
            scan_id=scan.id,
            component_version_id=cv.id,
            vulnerability_id=vuln.id,
            status=status,
        )
    )
    await session.commit()


async def _attach_license(
    session: AsyncSession,
    *,
    scan: Scan,
    cv: ComponentVersion,
    lic: License,
) -> None:
    session.add(
        LicenseFinding(
            scan_id=scan.id,
            component_version_id=cv.id,
            license_id=lic.id,
            kind="concluded",
            source_path=f"path-{unique_suffix()}",
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


async def _seed_empty_project(client: AsyncClient, *, team_id: uuid.UUID) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        project = await make_project(session, team=team)
        return project.id


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def test_diff_without_auth_returns_401(client) -> None:
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/diff",
        params={"base": str(uuid.uuid4()), "target": str(uuid.uuid4())},
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# Happy path — log4j removed + CVE resolved + new component (new CVE) introduced
# ---------------------------------------------------------------------------


async def test_diff_full_change_set(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    suffix = unique_suffix()
    factory = await _factory(client)
    async with factory() as session:
        base_dt = datetime(2026, 5, 20, 10, 0, tzinfo=UTC)
        target_dt = base_dt + timedelta(days=2)

        # BASE snapshot (v0.1): log4j 2.14.1 with a critical CVE + a permissive
        # MIT-licensed companion package (kept across both snapshots, unchanged).
        base_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=base_dt, release="v0.1"
        )
        log4j = await _get_or_make_component(
            session,
            purl=f"pkg:maven/org.apache.logging.log4j/log4j-core-{suffix}",
            name=f"log4j-core-{suffix}",
            namespace="org.apache.logging.log4j",
        )
        log4j_cv = await _get_or_make_cv(session, component=log4j, version="2.14.1")
        log4j_cve = await _get_or_make_vuln(
            session, external_id=f"CVE-2021-{suffix}", severity="critical"
        )
        await _attach_component(session, scan=base_scan, cv=log4j_cv)
        await _attach_finding(session, scan=base_scan, cv=log4j_cv, vuln=log4j_cve)

        keep = await _get_or_make_component(
            session, purl=f"pkg:npm/keep-{suffix}", name=f"keep-{suffix}"
        )
        keep_cv = await _get_or_make_cv(session, component=keep, version="1.0.0")
        mit = await _get_or_make_license(session, spdx_id="MIT", category="allowed")
        await _attach_component(session, scan=base_scan, cv=keep_cv)
        await _attach_license(session, scan=base_scan, cv=keep_cv, lic=mit)

        # TARGET snapshot (v0.2): log4j gone (CVE resolved), keep stays, and a NEW
        # package "newpkg" arrives carrying a NEW high CVE.
        target_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=target_dt, release="v0.2"
        )
        await _attach_component(session, scan=target_scan, cv=keep_cv)
        await _attach_license(session, scan=target_scan, cv=keep_cv, lic=mit)

        newpkg = await _get_or_make_component(
            session, purl=f"pkg:npm/newpkg-{suffix}", name=f"newpkg-{suffix}"
        )
        newpkg_cv = await _get_or_make_cv(session, component=newpkg, version="3.0.0")
        new_cve = await _get_or_make_vuln(
            session, external_id=f"CVE-2024-{suffix}", severity="high"
        )
        await _attach_component(session, scan=target_scan, cv=newpkg_cv)
        await _attach_finding(session, scan=target_scan, cv=newpkg_cv, vuln=new_cve)

        base_id, target_id = base_scan.id, target_scan.id

    response = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(base_id), "target": str(target_id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Anchors + labels.
    assert body["base"]["scan_id"] == str(base_id)
    assert body["base"]["release"] == "v0.1"
    assert body["target"]["scan_id"] == str(target_id)
    assert body["target"]["release"] == "v0.2"

    # Components: log4j removed, newpkg added, keep neither (unchanged).
    removed_names = {c["name"] for c in body["components"]["removed"]}
    added_names = {c["name"] for c in body["components"]["added"]}
    assert f"log4j-core-{suffix}" in removed_names
    assert f"newpkg-{suffix}" in added_names
    assert f"keep-{suffix}" not in removed_names
    assert f"keep-{suffix}" not in added_names
    assert body["components"]["changed"] == []

    # Vulnerabilities: old log4j CVE resolved, new CVE introduced.
    resolved_cves = {v["cve_id"] for v in body["vulnerabilities"]["resolved"]}
    introduced_cves = {v["cve_id"] for v in body["vulnerabilities"]["introduced"]}
    assert f"CVE-2021-{suffix}" in resolved_cves
    assert f"CVE-2024-{suffix}" in introduced_cves

    # Summary deltas: base had 1 critical component, target has 1 high component.
    assert body["summary"]["severity"]["critical"] == {"base": 1, "target": 0}
    assert body["summary"]["severity"]["high"] == {"base": 0, "target": 1}
    # component_count: base = log4j + keep = 2; target = keep + newpkg = 2.
    assert body["summary"]["component_count"] == {"base": 2, "target": 2}
    # Risk score (non-saturating, max of security/license axes):
    #   base 1 critical → security band 75–100, n=1 → 80.0
    #   target 1 high  → security band 50–74,  n=1 → 54.8
    assert body["summary"]["risk_score"] == {"base": 80.0, "target": 54.8}
    # Gate: base has a critical → fail; target has no critical/forbidden → pass.
    assert body["summary"]["gate"] == {"base": "fail", "target": "pass"}
    # Licenses: MIT (permissive) present in both (count 1 each); no prohibited.
    assert body["licenses"]["category_delta"]["permissive"] == {"base": 1, "target": 1}
    assert body["licenses"]["category_delta"]["prohibited"] == {"base": 0, "target": 0}
    assert body["truncated"] is False


# ---------------------------------------------------------------------------
# changed — version bump of the same package
# ---------------------------------------------------------------------------


async def test_diff_version_bump_is_changed_not_added_or_removed(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    suffix = unique_suffix()
    factory = await _factory(client)
    async with factory() as session:
        base_dt = datetime(2026, 5, 20, tzinfo=UTC)
        base_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=base_dt
        )
        target_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=base_dt + timedelta(days=1)
        )

        lodash = await _get_or_make_component(
            session, purl=f"pkg:npm/lodash-{suffix}", name=f"lodash-{suffix}"
        )
        old_cv = await _get_or_make_cv(session, component=lodash, version="4.17.20")
        new_cv = await _get_or_make_cv(session, component=lodash, version="4.17.21")
        await _attach_component(session, scan=base_scan, cv=old_cv)
        await _attach_component(session, scan=target_scan, cv=new_cv)

        base_id, target_id = base_scan.id, target_scan.id

    response = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(base_id), "target": str(target_id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["components"]["added"] == []
    assert body["components"]["removed"] == []
    assert len(body["components"]["changed"]) == 1
    changed = body["components"]["changed"][0]
    assert changed["name"] == f"lodash-{suffix}"
    assert changed["base_version"] == "4.17.20"
    assert changed["target_version"] == "4.17.21"
    # purl is the version-less package identity for a changed row.
    assert changed["purl"] == f"pkg:npm/lodash-{suffix}"


# ---------------------------------------------------------------------------
# base == target — all empty, summaries equal
# ---------------------------------------------------------------------------


async def test_diff_same_scan_is_all_empty(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    suffix = unique_suffix()
    factory = await _factory(client)
    async with factory() as session:
        scan = await _make_empty_scan(
            session, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC)
        )
        comp = await _get_or_make_component(
            session, purl=f"pkg:npm/solo-{suffix}", name=f"solo-{suffix}"
        )
        cv = await _get_or_make_cv(session, component=comp, version="1.0.0")
        cve = await _get_or_make_vuln(
            session, external_id=f"CVE-2023-{suffix}", severity="critical"
        )
        await _attach_component(session, scan=scan, cv=cv)
        await _attach_finding(session, scan=scan, cv=cv, vuln=cve)
        scan_id = scan.id

    response = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(scan_id), "target": str(scan_id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["components"] == {"added": [], "removed": [], "changed": []}
    assert body["vulnerabilities"] == {"introduced": [], "resolved": []}
    # Summary equal on both sides.
    assert body["summary"]["severity"]["critical"] == {"base": 1, "target": 1}
    assert body["summary"]["component_count"] == {"base": 1, "target": 1}
    assert body["summary"]["risk_score"]["base"] == body["summary"]["risk_score"]["target"]
    assert body["summary"]["gate"]["base"] == body["summary"]["gate"]["target"]
    assert body["truncated"] is False


# ---------------------------------------------------------------------------
# VEX / suppressed finding counts as not-open
# ---------------------------------------------------------------------------


async def test_diff_suppressed_finding_counts_as_resolved(client) -> None:
    # A finding that is OPEN in base but SUPPRESSED (via VEX) in target must read
    # as resolved (suppressed counts as not-open for the diff), even though the
    # same component_version is still present in both scans.
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    suffix = unique_suffix()
    factory = await _factory(client)
    async with factory() as session:
        base_dt = datetime(2026, 5, 20, tzinfo=UTC)
        base_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=base_dt
        )
        target_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=base_dt + timedelta(days=1)
        )
        comp = await _get_or_make_component(
            session, purl=f"pkg:npm/vexed-{suffix}", name=f"vexed-{suffix}"
        )
        cv = await _get_or_make_cv(session, component=comp, version="1.0.0")
        cve = await _get_or_make_vuln(
            session, external_id=f"CVE-2022-{suffix}", severity="high"
        )
        # Same component present in BOTH scans (so it is unchanged, not removed).
        await _attach_component(session, scan=base_scan, cv=cv)
        await _attach_component(session, scan=target_scan, cv=cv)
        # OPEN in base, SUPPRESSED in target.
        await _attach_finding(session, scan=base_scan, cv=cv, vuln=cve, status="new")
        await _attach_finding(
            session, scan=target_scan, cv=cv, vuln=cve, status="suppressed"
        )
        base_id, target_id = base_scan.id, target_scan.id

    response = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(base_id), "target": str(target_id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    resolved_cves = {v["cve_id"] for v in body["vulnerabilities"]["resolved"]}
    introduced_cves = {v["cve_id"] for v in body["vulnerabilities"]["introduced"]}
    assert f"CVE-2022-{suffix}" in resolved_cves
    assert f"CVE-2022-{suffix}" not in introduced_cves
    # The component itself is unchanged (present in both, same version).
    assert body["components"]["added"] == []
    assert body["components"]["removed"] == []
    assert body["components"]["changed"] == []


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_diff_other_team_member_is_forbidden(client) -> None:
    owner_team, _ = await _seed_team_with_user(client)
    _, outsider = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=owner_team.id)
    headers = _bearer_for(outsider)

    factory = await _factory(client)
    async with factory() as session:
        base_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC)
        )
        target_scan = await _make_empty_scan(
            session, project_id=project_id, created_at=datetime(2026, 5, 21, tzinfo=UTC)
        )
        base_id, target_id = base_scan.id, target_scan.id

    response = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(base_id), "target": str(target_id)},
    )
    # Anchors validate first (existence-hide), so an outsider whose base/target ARE
    # valid succeeded scans of this project is rejected at the RBAC layer (403);
    # the project itself is not existence-hidden (team membership is the signal).
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# IDOR + invalid-pin guards
# ---------------------------------------------------------------------------


async def test_diff_idor_other_project_scan_id_is_404(client) -> None:
    # A base/target id that belongs to ANOTHER project must be existence-hidden 404.
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    other_team, _ = await _seed_team_with_user(client)
    other_project_id = await _seed_empty_project(client, team_id=other_team.id)

    factory = await _factory(client)
    async with factory() as session:
        mine = await _make_empty_scan(
            session, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC)
        )
        foreign = await _make_empty_scan(
            session,
            project_id=other_project_id,
            created_at=datetime(2026, 5, 21, tzinfo=UTC),
        )
        mine_id, foreign_id = mine.id, foreign.id

    # foreign as target.
    r1 = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(mine_id), "target": str(foreign_id)},
    )
    assert r1.status_code == 404, r1.text
    assert r1.headers["content-type"].startswith(PROBLEM_JSON)

    # foreign as base.
    r2 = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(foreign_id), "target": str(mine_id)},
    )
    assert r2.status_code == 404, r2.text


async def test_diff_non_succeeded_scan_id_is_404(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    factory = await _factory(client)
    async with factory() as session:
        ok = await _make_empty_scan(
            session, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC)
        )
        failed = await _make_empty_scan(
            session,
            project_id=project_id,
            created_at=datetime(2026, 5, 21, tzinfo=UTC),
            status="failed",
        )
        ok_id, failed_id = ok.id, failed.id

    response = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(ok_id), "target": str(failed_id)},
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_diff_nonexistent_scan_id_is_404(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    factory = await _factory(client)
    async with factory() as session:
        ok = await _make_empty_scan(
            session, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC)
        )
        ok_id = ok.id

    response = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(ok_id), "target": str(uuid.uuid4())},
    )
    assert response.status_code == 404, response.text


async def test_diff_unknown_project_is_404(client) -> None:
    team, user = await _seed_team_with_user(client)
    headers = _bearer_for(user)
    # An unknown project with random scan ids: anchors resolve against the unknown
    # project and fail (no succeeded scan of THAT project) → existence-hide 404.
    response = await client.get(
        f"/v1/projects/{uuid.uuid4()}/diff",
        headers=headers,
        params={"base": str(uuid.uuid4()), "target": str(uuid.uuid4())},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_diff_missing_param_is_422(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    # Missing target.
    r1 = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"base": str(uuid.uuid4())},
    )
    assert r1.status_code == 422, r1.text

    # Missing base.
    r2 = await client.get(
        f"/v1/projects/{project_id}/diff",
        headers=headers,
        params={"target": str(uuid.uuid4())},
    )
    assert r2.status_code == 422, r2.text

    # Both missing.
    r3 = await client.get(f"/v1/projects/{project_id}/diff", headers=headers)
    assert r3.status_code == 422, r3.text
