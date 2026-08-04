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
    ref: str | None = None,
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
            ref=ref,
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


async def _seed_empty_project(
    client: AsyncClient, *, team_id: uuid.UUID, default_branch: str | None = None
) -> uuid.UUID:
    factory = await _factory(client)
    async with factory() as session:
        team = (await session.execute(select(Team).where(Team.id == team_id))).scalar_one()
        project = await make_project(session, team=team)
        if default_branch is not None:
            project.default_branch = default_branch
            await session.commit()
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


async def test_releases_release_filter_returns_only_that_version(client) -> None:
    # "Which snapshot is 4.0?" — the whole point of attaching a version label.
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)
    base = datetime(2026, 5, 20, tzinfo=UTC)

    tagged = await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base, release="4.0"
    )
    await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base + timedelta(days=1), release="4.1"
    )
    await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base + timedelta(days=2)
    )

    unfiltered = await client.get(f"/v1/projects/{project_id}/releases", headers=headers)
    assert unfiltered.json()["total"] == 3

    filtered = await client.get(
        f"/v1/projects/{project_id}/releases", headers=headers, params={"release": "4.0"}
    )
    assert filtered.status_code == 200, filtered.text
    body = filtered.json()
    # `total` must describe the FILTERED set — a caller paging the filter must
    # not be told there are three matches.
    assert body["total"] == 1
    assert [row["scan_id"] for row in body["items"]] == [str(tagged)]
    assert body["items"][0]["release"] == "4.0"


async def test_releases_release_filter_trims_both_sides(client) -> None:
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)
    tagged = await _seed_succeeded_scan(
        client,
        project_id=project_id,
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
        release=" 4.0 ",
    )

    for query in ("4.0", "  4.0  "):
        response = await client.get(
            f"/v1/projects/{project_id}/releases",
            headers=headers,
            params={"release": query},
        )
        assert response.status_code == 200, response.text
        assert [row["scan_id"] for row in response.json()["items"]] == [str(tagged)]


async def test_releases_unknown_release_is_empty_200_not_404(client) -> None:
    # Absence of a version is a normal answer. A 404 here would be
    # indistinguishable from "no such project".
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)
    await _seed_succeeded_scan(
        client,
        project_id=project_id,
        created_at=datetime(2026, 5, 20, tzinfo=UTC),
        release="4.0",
    )

    response = await client.get(
        f"/v1/projects/{project_id}/releases",
        headers=headers,
        params={"release": "9.9"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


async def test_releases_release_filter_does_not_cross_projects(client) -> None:
    team, user = await _seed_team_with_user(client)
    mine = await _seed_empty_project(client, team_id=team.id)
    theirs = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await _seed_succeeded_scan(client, project_id=theirs, created_at=base, release="4.0")

    response = await client.get(
        f"/v1/projects/{mine}/releases", headers=headers, params={"release": "4.0"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


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


async def test_notice_anchor_pins_older_scan(client) -> None:
    # The NOTICE is the artefact a shipped release is judged by, so it has to
    # honour the same pin the tab above it does. Without the anchor, "give me
    # the NOTICE we shipped with the previous release" stops being answerable
    # the moment a newer scan succeeds.
    user, project_id, older, _latest = await _seed_two_snapshot_project(client)
    headers = _bearer_for(user)

    # Default (latest succeeded): no license findings on that scan at all.
    default = await client.get(f"/v1/projects/{project_id}/notice", headers=headers)
    assert default.status_code == 200, default.text
    assert default.headers["x-notice-license-count"] == "0"
    assert "GPL-3.0-only" not in default.text

    # Pinned to the OLDER scan: its forbidden license must be credited.
    pinned = await client.get(
        f"/v1/projects/{project_id}/notice",
        headers=headers,
        params={"scan_id": str(older)},
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.headers["x-notice-license-count"] == "1"
    assert "GPL-3.0-only" in pinned.text


async def test_notice_anchor_non_succeeded_scan_id_is_404(client) -> None:
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

    for pinned_scan_id in (failed_scan, uuid.uuid4()):
        response = await client.get(
            f"/v1/projects/{project_id}/notice",
            headers=headers,
            params={"scan_id": str(pinned_scan_id)},
        )
        assert response.status_code == 404, response.text
        assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_omitting_scan_id_returns_latest_succeeded(client) -> None:
    user, project_id, older, latest = await _seed_two_snapshot_project(client)
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project_id}/overview", headers=headers)
    assert response.status_code == 200, response.text
    # latest_succeeded_scan_at reflects the LATEST scan, never the older pin.
    assert response.json()["last_succeeded_scan_at"] is not None


# ---------------------------------------------------------------------------
# Current-state anchor follows the project's main line
# ---------------------------------------------------------------------------


async def _seed_two_branch_project(client: AsyncClient, *, default_branch: str | None):
    """Main line scanned first (2 critical), another branch scanned LAST (1 high).

    The release branch finishing last is the whole point: under a purely
    recency-based anchor it would decide the project's current state.
    """
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(
        client, team_id=team.id, default_branch=default_branch
    )
    base = datetime(2026, 5, 20, tzinfo=UTC)
    main_scan = await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base, n_critical=2, ref="main"
    )
    release_scan = await _seed_succeeded_scan(
        client,
        project_id=project_id,
        created_at=base + timedelta(days=2),
        n_high=1,
        ref="release/1.x",
    )
    return user, project_id, main_scan, release_scan


async def test_anchor_prefers_main_line_over_a_newer_other_branch(client) -> None:
    user, project_id, _main_scan, _release_scan = await _seed_two_branch_project(
        client, default_branch="main"
    )
    headers = _bearer_for(user)

    overview = await client.get(f"/v1/projects/{project_id}/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    # main's 2 critical, not release/1.x's 1 high, even though the latter is newer.
    assert overview.json()["severity_distribution"]["critical"] == 2
    assert overview.json()["severity_distribution"]["high"] == 0


async def test_gate_verdict_is_not_decided_by_another_branch(client) -> None:
    # The defect that motivated this: main's CI asks for its verdict and gets
    # the release branch's, because that branch scanned more recently.
    user, project_id, main_scan, release_scan = await _seed_two_branch_project(
        client, default_branch="main"
    )
    headers = _bearer_for(user)

    default = await client.get(f"/v1/projects/{project_id}/gate-result", headers=headers)
    assert default.status_code == 200, default.text
    assert default.json()["scan_id"] == str(main_scan)
    assert default.json()["critical_cve_count"] == 2

    # A CI job on the release branch names its own ref and gets its own verdict.
    pinned = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=headers,
        params={"ref": "release/1.x"},
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["scan_id"] == str(release_scan)
    assert pinned.json()["critical_cve_count"] == 0


async def test_gate_ref_accepts_a_fully_qualified_ref(client) -> None:
    # CI passes $GITHUB_REF; the endpoint must normalize it the same way the
    # scan-create path did, or the branch would never match its own scans.
    user, project_id, main_scan, _release = await _seed_two_branch_project(
        client, default_branch="release/1.x"
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=headers,
        params={"ref": "refs/heads/main"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["scan_id"] == str(main_scan)


async def test_anchor_falls_back_when_no_scan_is_on_the_main_line(client) -> None:
    # A project whose main line is 'trunk' matches nothing, so the guess must
    # degrade to the pre-existing "newest succeeded scan" rule, not to nothing.
    user, project_id, _main, release_scan = await _seed_two_branch_project(
        client, default_branch="trunk"
    )
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project_id}/gate-result", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["scan_id"] == str(release_scan)


async def test_anchor_defaults_to_main_when_default_branch_is_unset(client) -> None:
    # default_branch is NULL on most projects (the create form never asks), so
    # the fallback to 'main' is what makes the fix reach them at all.
    user, project_id, main_scan, _release = await _seed_two_branch_project(
        client, default_branch=None
    )
    headers = _bearer_for(user)

    response = await client.get(f"/v1/projects/{project_id}/gate-result", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["scan_id"] == str(main_scan)


async def test_refless_scans_are_unaffected_by_the_main_line_preference(client) -> None:
    # Ad-hoc scans carry ref=NULL. NULL must not be treated as matching 'main'
    # (SQL NULL sorts first under DESC), so these projects keep pure recency.
    team, user = await _seed_team_with_user(client)
    project_id = await _seed_empty_project(client, team_id=team.id)
    headers = _bearer_for(user)
    base = datetime(2026, 5, 20, tzinfo=UTC)
    await _seed_succeeded_scan(client, project_id=project_id, created_at=base, n_critical=2)
    newest = await _seed_succeeded_scan(
        client, project_id=project_id, created_at=base + timedelta(days=1), n_high=1
    )

    response = await client.get(f"/v1/projects/{project_id}/gate-result", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["scan_id"] == str(newest)


async def test_gate_ref_with_no_succeeded_scan_does_not_borrow_another_branch(
    client,
) -> None:
    user, project_id, _main, _release = await _seed_two_branch_project(
        client, default_branch="main"
    )
    headers = _bearer_for(user)

    response = await client.get(
        f"/v1/projects/{project_id}/gate-result",
        headers=headers,
        params={"ref": "feature/nope"},
    )
    assert response.status_code == 200, response.text
    # No signal for that branch → the documented no-scan pass, and crucially
    # NOT main's two criticals.
    assert response.json()["scan_id"] is None
    assert response.json()["gate"] == "pass"
    assert response.json()["critical_cve_count"] == 0


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
        f"/v1/projects/{project_id}/notice",
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
