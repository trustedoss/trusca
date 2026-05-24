"""
DB-backed integration tests for the npm remediation service + endpoint —
v2.2 2.2-b2 (``services.remediation_service`` + ``api.v1.remediation``).

Driven against the live Postgres (CLAUDE.md core rule #1 — no SQLite, no mocking
our own infra) with a session bound to ``DATABASE_URL``; skipped when unset. The
recommendation aggregation reuses the a3 engine over real findings; the manifest
is supplied via the override path (the preserved-source fetch is exercised
separately at the unit level since it needs a real tarball on disk).

Covered:
  * happy path — vulnerable npm dep bumped in the supplied manifest,
  * RBAC — a non-member is blocked with 404 (existence-hide),
  * missing-manifest path — no override + no preserved source → manifest_found
    False, no crash,
  * recommendations-empty path — a project with no open npm findings → no edit,
  * scoped-package matching — a3 ``Component.name`` loses the scope but our purl
    decode keeps it, so ``@scope/pkg`` is matched against the manifest,
  * malformed override manifest → 422 RFC 7807,
  * non-member endpoint call → 404 RFC 7807 problem shape.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import AsyncIterator
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
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip remediation-service tests")
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
            "alembic upgrade head failed; remediation tests cannot run\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


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


# ---------------------------------------------------------------------------
# Seed helpers (mirror test_comment_recommendations.py)
# ---------------------------------------------------------------------------


async def _make_npm_cv(session: AsyncSession, *, name: str, version: str):
    """Create an npm Component + ComponentVersion. ``name`` is the bare/scoped
    package name; the purl encodes it the way cdxgen does."""
    from models import Component, ComponentVersion

    # cdxgen leaves the scope as-is in the purl for our fixtures.
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


async def _make_vuln(session: AsyncSession, *, cve_id: str, severity: str = "high"):
    from models import Vulnerability

    v = Vulnerability(external_id=cve_id, source="NVD", severity=severity, summary=cve_id)
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _attach_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vuln_id: uuid.UUID,
    fixed_version: str | None,
    status: str = "new",
):
    from models import VulnerabilityFinding

    vf = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        vulnerability_id=vuln_id,
        status=status,
        fixed_version=fixed_version,
    )
    session.add(vf)
    await session.commit()
    await session.refresh(vf)
    return vf


async def _attach_scan_component(session: AsyncSession, *, scan_id, cv_id, direct=True, depth=1):
    from models import ScanComponent

    sc = ScanComponent(scan_id=scan_id, component_version_id=cv_id, direct=direct, depth=depth)
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


async def _project_with_scan(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    owner = await make_user(session)
    await make_membership(session, user=owner, team=team, role="developer")
    project = await make_project(session, team=team, created_by=owner)
    scan = await make_scan(session, project=project, status="succeeded")
    # Point the project at its latest scan (the service reads latest_scan_id).
    project.latest_scan_id = scan.id
    await session.commit()
    await session.refresh(project)
    return org, team, owner, project, scan


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


async def test_happy_path_bumps_vulnerable_dep(db_session: AsyncSession) -> None:
    from services.remediation_service import compute_npm_dry_run

    _, team, owner, project, scan = await _project_with_scan(db_session)
    suffix = unique_suffix()
    pkg = f"lodash-{suffix}"

    cv = await _make_npm_cv(db_session, name=pkg, version="4.17.20")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vuln(db_session, cve_id=f"CVE-{suffix}", severity="critical")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v.id, fixed_version="4.17.21"
    )

    actor = principal_for(owner, team_ids=[team.id], role="developer")
    manifest = json.dumps({"dependencies": {pkg: "^4.17.20"}}, indent=2) + "\n"
    result = await compute_npm_dry_run(db_session, actor, project.id, manifest_override=manifest)

    assert result.changed is True
    assert result.manifest_source == "override"
    assert result.manifest_found is True
    assert any(
        r.package == pkg and r.recommended_version == "4.17.21" for r in result.recommendations
    )
    edited = json.loads(result.edited_manifest)
    assert edited["dependencies"][pkg] == "^4.17.21"
    assert any(w.code == "lockfile_regeneration_required" for w in result.warnings)


async def test_scoped_package_matched_via_purl(db_session: AsyncSession) -> None:
    """``Component.name`` for ``@scope/pkg`` is the bare name, but the purl keeps
    the scope. The service decodes the purl so the manifest key matches."""
    from services.remediation_service import compute_npm_dry_run

    _, team, owner, project, scan = await _project_with_scan(db_session)
    suffix = unique_suffix()
    scoped = f"@scope/pkg-{suffix}"

    cv = await _make_npm_cv(db_session, name=scoped, version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vuln(db_session, cve_id=f"CVE-s-{suffix}")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v.id, fixed_version="1.2.0"
    )

    actor = principal_for(owner, team_ids=[team.id], role="developer")
    manifest = json.dumps({"dependencies": {scoped: "^1.0.0"}}, indent=2) + "\n"
    result = await compute_npm_dry_run(db_session, actor, project.id, manifest_override=manifest)
    assert result.changed is True
    assert json.loads(result.edited_manifest)["dependencies"][scoped] == "^1.2.0"


async def test_non_member_blocked_404(db_session: AsyncSession) -> None:
    from services.remediation_service import (
        ProjectNotAccessible,
        compute_npm_dry_run,
    )

    _, team, _owner, project, _scan = await _project_with_scan(db_session)
    outsider = await make_user(db_session)
    actor = principal_for(outsider, team_ids=[], role="developer")
    with pytest.raises(ProjectNotAccessible):
        await compute_npm_dry_run(
            db_session, actor, project.id, manifest_override='{"dependencies": {}}'
        )


async def test_unknown_project_404(db_session: AsyncSession) -> None:
    from services.remediation_service import (
        ProjectNotAccessible,
        compute_npm_dry_run,
    )

    user = await make_user(db_session)
    actor = principal_for(user, team_ids=[], role="developer")
    with pytest.raises(ProjectNotAccessible):
        await compute_npm_dry_run(
            db_session, actor, uuid.uuid4(), manifest_override='{"dependencies": {}}'
        )


async def test_no_manifest_available(db_session: AsyncSession) -> None:
    from services.remediation_service import compute_npm_dry_run

    _, team, owner, project, scan = await _project_with_scan(db_session)
    suffix = unique_suffix()
    cv = await _make_npm_cv(db_session, name=f"pkg-{suffix}", version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vuln(db_session, cve_id=f"CVE-nm-{suffix}")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v.id, fixed_version="1.1.0"
    )

    actor = principal_for(owner, team_ids=[team.id], role="developer")
    # No override + no preserved tarball on disk → manifest not found, no crash.
    result = await compute_npm_dry_run(db_session, actor, project.id)
    assert result.manifest_found is False
    assert result.manifest_source == "none"
    assert result.edited_manifest is None
    assert result.changed is False
    # Recommendations are still surfaced so the UI can explain what WOULD bump.
    assert any(r.package == f"pkg-{suffix}" for r in result.recommendations)
    assert any("no package.json" in n for n in result.notes)


async def test_recommendations_empty_noop(db_session: AsyncSession) -> None:
    from services.remediation_service import compute_npm_dry_run

    _, team, owner, project, _scan = await _project_with_scan(db_session)
    actor = principal_for(owner, team_ids=[team.id], role="developer")
    manifest = json.dumps({"dependencies": {"untouched": "^1.0.0"}}, indent=2) + "\n"
    result = await compute_npm_dry_run(db_session, actor, project.id, manifest_override=manifest)
    assert result.recommendations == ()
    assert result.changed is False
    # No recommendations → nothing to bump, manifest returned unchanged-state.
    assert result.manifest_found is True


async def test_no_scan_no_recommendations(db_session: AsyncSession) -> None:
    from services.remediation_service import compute_npm_dry_run

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    owner = await make_user(db_session)
    await make_membership(db_session, user=owner, team=team, role="developer")
    project = await make_project(db_session, team=team, created_by=owner)
    actor = principal_for(owner, team_ids=[team.id], role="developer")

    result = await compute_npm_dry_run(
        db_session, actor, project.id, manifest_override='{"dependencies": {}}'
    )
    assert result.scan_id is None
    assert result.recommendations == ()
    assert any("no completed scan" in n for n in result.notes)


async def test_malformed_override_rejected(db_session: AsyncSession) -> None:
    from services.remediation_service import ManifestRejected, compute_npm_dry_run

    _, team, owner, project, scan = await _project_with_scan(db_session)
    suffix = unique_suffix()
    cv = await _make_npm_cv(db_session, name=f"pkg-{suffix}", version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vuln(db_session, cve_id=f"CVE-bad-{suffix}")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v.id, fixed_version="1.1.0"
    )
    actor = principal_for(owner, team_ids=[team.id], role="developer")
    with pytest.raises(ManifestRejected):
        await compute_npm_dry_run(db_session, actor, project.id, manifest_override="{not json")


async def test_closed_findings_excluded(db_session: AsyncSession) -> None:
    """A finding dispositioned not_affected/fixed/false_positive does not drive a
    recommendation — exactly like the build gate."""
    from services.remediation_service import compute_npm_dry_run

    _, team, owner, project, scan = await _project_with_scan(db_session)
    suffix = unique_suffix()
    pkg = f"closed-{suffix}"
    cv = await _make_npm_cv(db_session, name=pkg, version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vuln(db_session, cve_id=f"CVE-cl-{suffix}")
    await _attach_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vuln_id=v.id,
        fixed_version="1.5.0",
        status="not_affected",
    )
    actor = principal_for(owner, team_ids=[team.id], role="developer")
    manifest = json.dumps({"dependencies": {pkg: "^1.0.0"}}, indent=2) + "\n"
    result = await compute_npm_dry_run(db_session, actor, project.id, manifest_override=manifest)
    assert result.recommendations == ()
    assert result.changed is False


# ---------------------------------------------------------------------------
# Endpoint test (RFC 7807 shape + auth) — real JWT via ASGITransport
# ---------------------------------------------------------------------------


def _bearer_for(user) -> dict[str, str]:
    from core.security import create_access_token

    role = "super_admin" if user.is_superuser else None
    token = create_access_token(subject=str(user.id), role=role)
    return {"Authorization": f"Bearer {token}"}


async def test_endpoint_requires_auth(db_session: AsyncSession) -> None:
    from main import app

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/v1/projects/{uuid.uuid4()}/remediation/npm/dry-run",
            json={"manifest": '{"dependencies": {}}'},
        )
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


async def test_endpoint_unknown_project_returns_problem_json(
    db_session: AsyncSession,
) -> None:
    """A logged-in user hitting an unknown project gets a 404 RFC 7807 problem
    with the existence-hide shape (a real JWT drives the request path)."""
    from main import app

    user = await make_user(db_session)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/v1/projects/{uuid.uuid4()}/remediation/npm/dry-run",
            json={"manifest": '{"dependencies": {}}'},
            headers=_bearer_for(user),
        )

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    for key in ("type", "title", "status", "detail", "instance"):
        assert key in body
    assert body["status"] == 404


async def test_endpoint_happy_path_returns_dry_run(db_session: AsyncSession) -> None:
    """Full request path: seed a vulnerable npm dep, then POST an override
    manifest and assert the response carries the edited package.json."""
    from main import app

    _, team, owner, project, scan = await _project_with_scan(db_session)
    suffix = unique_suffix()
    pkg = f"axios-{suffix}"
    cv = await _make_npm_cv(db_session, name=pkg, version="0.21.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    v = await _make_vuln(db_session, cve_id=f"CVE-ep-{suffix}", severity="high")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v.id, fixed_version="0.21.1"
    )

    manifest = json.dumps({"dependencies": {pkg: "^0.21.0"}}, indent=2) + "\n"
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/v1/projects/{project.id}/remediation/npm/dry-run",
            json={"manifest": manifest},
            headers=_bearer_for(owner),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["changed"] is True
    assert body["ecosystem"] == "npm"
    assert json.loads(body["edited_manifest"])["dependencies"][pkg] == "^0.21.1"
