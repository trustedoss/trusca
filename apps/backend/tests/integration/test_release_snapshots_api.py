"""
Integration tests for release-snapshot viewing — feature #28 Phase 1.

Covers:
  - GET /v1/projects/{id}/releases  (the Releases table data source)
  - the optional ``?scan_id=`` snapshot anchor on the detail read endpoints
    (overview / components / vulnerabilities / licenses / gate-result), incl.
    the IDOR guard (another project's scan id → 404) and the non-succeeded
    scan id → 404 guard.

Diff / compare between releases is a LATER phase and is intentionally not tested
here.

These run against the real Postgres (CLAUDE.md core rule #1 — no SQLite). Each
test seeds its own org → team → user → project graph with one or more SUCCEEDED
scans, attaching components / CVE findings / license findings keyed by scan_id so
two snapshots of the same project carry DIFFERENT numbers (the whole point of
pinning).
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
        pytest.skip("DATABASE_URL not set — skip release-snapshot API tests")
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
            f"alembic upgrade head failed; release-snapshot API tests cannot run\n"
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
# Seeding helpers — build a succeeded scan carrying N critical CVE components +
# optional forbidden license + an optional release label, with a controllable
# created_at so ordering / "older vs latest" is deterministic.
# ---------------------------------------------------------------------------


async def _make_cv(session: AsyncSession) -> ComponentVersion:
    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
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
    return cv


async def _make_vuln(session: AsyncSession, *, severity: str) -> Vulnerability:
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
    return v


async def _get_or_make_license(session: AsyncSession, *, spdx_id: str, category: str) -> License:
    existing = await session.scalar(select(License).where(License.spdx_id == spdx_id))
    if existing is not None:
        return existing
    lic = License(spdx_id=spdx_id, name=spdx_id, category=category)
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    return lic


async def _seed_succeeded_scan(
    client: AsyncClient,
    *,
    project_id: uuid.UUID,
    created_at: datetime,
    n_critical: int = 0,
    n_high: int = 0,
    forbidden_license: bool = False,
    release: str | None = None,
    status: str = "succeeded",
) -> uuid.UUID:
    """Create a scan (default succeeded) with components + findings keyed to it.

    Each critical/high CVE gets its OWN component_version (so severity_summary
    counts components, not findings). A forbidden license is attached to the
    first component when requested.
    """
    factory = await _factory(client)
    async with factory() as session:
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

        first_cv_id: uuid.UUID | None = None
        for severity, count in (("critical", n_critical), ("high", n_high)):
            for _ in range(count):
                cv = await _make_cv(session)
                if first_cv_id is None:
                    first_cv_id = cv.id
                session.add(
                    ScanComponent(
                        scan_id=scan.id,
                        component_version_id=cv.id,
                        direct=True,
                        depth=1,
                        raw_data={},
                    )
                )
                vuln = await _make_vuln(session, severity=severity)
                session.add(
                    VulnerabilityFinding(
                        scan_id=scan.id,
                        component_version_id=cv.id,
                        vulnerability_id=vuln.id,
                    )
                )
                await session.commit()

        if forbidden_license:
            if first_cv_id is None:
                cv = await _make_cv(session)
                first_cv_id = cv.id
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
            lic = await _get_or_make_license(session, spdx_id="GPL-3.0-only", category="forbidden")
            session.add(
                LicenseFinding(
                    scan_id=scan.id,
                    component_version_id=first_cv_id,
                    license_id=lic.id,
                    kind="concluded",
                    source_path=f"path-{unique_suffix()}",
                )
            )
            await session.commit()

        return scan.id


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


async def test_releases_without_auth_returns_401(client) -> None:
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/releases")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# GET /v1/projects/{id}/releases
# ---------------------------------------------------------------------------


async def test_releases_lists_succeeded_scans_newest_first_with_summaries(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    base = datetime(2026, 5, 20, 10, 0, tzinfo=UTC)
    # Older snapshot: 2 critical + forbidden license, with a release label.
    older = await _seed_succeeded_scan(
        client,
        project_id=project_id,
        created_at=base,
        n_critical=2,
        forbidden_license=True,
        release="v1.0.0",
    )
    # Newer snapshot: 1 high only, NO release label (absent → null).
    newer = await _seed_succeeded_scan(
        client,
        project_id=project_id,
        created_at=base + timedelta(days=2),
        n_high=1,
    )
    # A FAILED and a RUNNING scan must be excluded from /releases entirely.
    await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base + timedelta(days=3), status="failed"
    )
    await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base + timedelta(days=4), status="running"
    )

    response = await client.get(f"/v1/projects/{project_id}/releases", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 2  # only the two succeeded scans
    assert [row["scan_id"] for row in body["items"]] == [str(newer), str(older)]

    newer_row, older_row = body["items"]

    # Newest first; release null when absent.
    assert newer_row["release"] is None
    assert newer_row["severity_summary"] == {"critical": 0, "high": 1, "medium": 0, "low": 0}
    assert newer_row["component_count"] == 1
    assert newer_row["gate_status"] == "pass"  # no critical / forbidden → pass
    assert newer_row["risk_score"] == 54.8  # 1 high → security band 50–74, n=1 → 54.8

    # Older snapshot carries its release label + critical/forbidden → gate fail.
    assert older_row["release"] == "v1.0.0"
    assert older_row["severity_summary"] == {"critical": 2, "high": 0, "medium": 0, "low": 0}
    assert older_row["component_count"] == 2
    assert older_row["gate_status"] == "fail"  # 2 critical (and forbidden license)
    # Security 2 critical → 83.3 (band 75–100, n=2); License 1 forbidden → 80.0;
    # overall = max = 83.3.
    assert older_row["risk_score"] == 83.3


async def test_releases_empty_when_no_succeeded_scan(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)

    # Only a failed scan exists — /releases must be an empty 200, not a 404.
    await _seed_succeeded_scan(
        client,
        project_id=project_id,
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
        status="failed",
    )

    response = await client.get(f"/v1/projects/{project_id}/releases", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_releases_other_team_member_is_forbidden(client) -> None:
    # RBAC: a non-member of the owning team is rejected (mirrors overview → 403).
    owner_team, _ = await _seed_team_with_user(client)
    _, outsider = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=owner_team.id)
    await _seed_succeeded_scan(
        client, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC), n_high=1
    )
    headers = _bearer_for(outsider)

    response = await client.get(f"/v1/projects/{project_id}/releases", headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_releases_unknown_project_is_404(client) -> None:
    team, user = await _seed_team_with_user(client)
    headers = _bearer_for(user)
    response = await client.get(f"/v1/projects/{uuid.uuid4()}/releases", headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# ---------------------------------------------------------------------------
# ?scan_id= snapshot anchor on the detail endpoints
# ---------------------------------------------------------------------------


async def _seed_two_snapshot_project(client: AsyncClient):
    """Project with an OLDER succeeded scan (2 critical) and a LATEST (1 high).

    Returns ``(user, project_id, older_scan_id, latest_scan_id)``.
    """
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    older = await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base, n_critical=2, forbidden_license=True
    )
    latest = await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base + timedelta(days=2), n_high=1
    )
    return user, project_id, older, latest


async def test_overview_anchor_pins_older_scan(client) -> None:
    user, project_id, older, latest = await _seed_two_snapshot_project(client)
    headers = _bearer_for(user)

    # Default (no scan_id) → latest succeeded: 1 high component.
    default = await client.get(f"/v1/projects/{project_id}/overview", headers=headers)
    assert default.status_code == 200, default.text
    assert default.json()["severity_distribution"]["high"] == 1
    assert default.json()["severity_distribution"]["critical"] == 0

    # Pinned to the OLDER scan → 2 critical components instead.
    pinned = await client.get(
        f"/v1/projects/{project_id}/overview",
        headers=headers,
        params={"scan_id": str(older)},
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["severity_distribution"]["critical"] == 2
    assert pinned.json()["severity_distribution"]["high"] == 0


async def test_components_and_vulns_and_licenses_anchor_pins_older_scan(client) -> None:
    user, project_id, older, latest = await _seed_two_snapshot_project(client)
    headers = _bearer_for(user)

    # Components: latest has 1 component, older has 2 (+1 with forbidden license).
    latest_components = await client.get(
        f"/v1/projects/{project_id}/components", headers=headers
    )
    assert latest_components.json()["total"] == 1
    older_components = await client.get(
        f"/v1/projects/{project_id}/components", headers=headers, params={"scan_id": str(older)}
    )
    assert older_components.json()["total"] == 2

    # Vulnerabilities: latest 1 finding, older 2 findings.
    latest_vulns = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities", headers=headers
    )
    assert latest_vulns.json()["total"] == 1
    older_vulns = await client.get(
        f"/v1/projects/{project_id}/vulnerabilities",
        headers=headers,
        params={"scan_id": str(older)},
    )
    assert older_vulns.json()["total"] == 2

    # Licenses: only the older snapshot carries a (forbidden) license finding.
    latest_licenses = await client.get(
        f"/v1/projects/{project_id}/licenses", headers=headers
    )
    assert latest_licenses.json()["total"] == 0
    older_licenses = await client.get(
        f"/v1/projects/{project_id}/licenses", headers=headers, params={"scan_id": str(older)}
    )
    assert older_licenses.json()["total"] == 1


async def test_gate_result_anchor_reflects_pinned_snapshot(client) -> None:
    user, project_id, older, latest = await _seed_two_snapshot_project(client)
    headers = _bearer_for(user)

    # Default (latest succeeded): 1 high → pass.
    default = await client.get(f"/v1/projects/{project_id}/gate-result", headers=headers)
    assert default.status_code == 200, default.text
    assert default.json()["gate"] == "pass"
    assert default.json()["scan_id"] == str(latest)

    # Pinned older: 2 critical (+ forbidden license) → fail.
    pinned = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=headers,
        params={"scan_id": str(older)},
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["gate"] == "fail"
    assert pinned.json()["scan_id"] == str(older)
    assert pinned.json()["critical_cve_count"] == 2


async def test_omitting_scan_id_returns_latest_succeeded(client) -> None:
    user, project_id, older, latest = await _seed_two_snapshot_project(client)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project_id}/overview", headers=headers)
    assert response.status_code == 200, response.text
    # latest_succeeded_scan_at reflects the LATEST scan, never the older pin.
    assert response.json()["last_succeeded_scan_at"] is not None


# ---------------------------------------------------------------------------
# IDOR + invalid-pin guards
# ---------------------------------------------------------------------------


async def test_anchor_idor_other_project_scan_id_is_404(client) -> None:
    # A scan id that belongs to ANOTHER project must never be readable through
    # this project's surface — existence-hidden as 404 across every detail tab.
    user, project_id, _older, _latest = await _seed_two_snapshot_project(client)
    headers = _bearer_for(user)

    # Build a SEPARATE project (different team) with its own succeeded scan.
    other_team, _ = await _seed_team_with_user(client)
    other_project_id = await _seed_empty_project(client, team_id=other_team.id)
    foreign_scan = await _seed_succeeded_scan(
        client,
        project_id=other_project_id,
        created_at=datetime(2026, 5, 21, tzinfo=UTC),
        n_critical=3,
    )

    for path in (
        f"/v1/projects/{project_id}/overview",
        f"/v1/projects/{project_id}/components",
        f"/v1/projects/{project_id}/vulnerabilities",
        f"/v1/projects/{project_id}/licenses",
        f"/v1/projects/{project_id}/obligations",
        f"/v1/projects/{project_id}/gate-result",
        f"/v1/projects/{project_id}/sbom",
    ):
        response = await client.get(
            path, headers=headers, params={"scan_id": str(foreign_scan)}
        )
        assert response.status_code == 404, f"{path} -> {response.status_code} {response.text}"
        assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_anchor_non_succeeded_scan_id_of_this_project_is_404(client) -> None:
    # A scan id that DOES belong to this project but is not succeeded → 404
    # (no immutable snapshot to read).
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)
    await _seed_succeeded_scan(
        client, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC), n_high=1
    )
    failed_scan = await _seed_succeeded_scan(
        client,
        project_id=project_id,
        created_at=datetime(2026, 5, 22, tzinfo=UTC),
        status="failed",
    )

    response = await client.get(
        f"/v1/projects/{project_id}/overview",
        headers=headers,
        params={"scan_id": str(failed_scan)},
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_anchor_nonexistent_scan_id_is_404(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)
    await _seed_succeeded_scan(
        client, project_id=project_id, created_at=datetime(2026, 5, 20, tzinfo=UTC), n_high=1
    )

    response = await client.get(
        f"/v1/projects/{project_id}/overview",
        headers=headers,
        params={"scan_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404, response.text
    assert response.headers["content-type"].startswith(PROBLEM_JSON)
