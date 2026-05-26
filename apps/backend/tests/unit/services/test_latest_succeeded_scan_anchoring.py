"""
Regression: project-detail read paths must anchor on the latest *succeeded*
scan, not ``Project.latest_scan_id`` (the last *attempted* scan).

The verified production bug (project ``ci-vulns``): an OLDER succeeded source
scan carrying 74 findings (10 critical) followed by two NEWER FAILED scans made
the whole project view contradict itself — Overview read "0 / 100 NO RISK,
0 components", the Components / Vulnerabilities tabs read "0 of 0", yet the Build
gate (which already resolved the latest *succeeded* scan) read "Fail — blocked,
10 open critical CVE(s)".

These tests reproduce that exact shape — an earlier succeeded scan with findings,
then a later FAILED scan AND a later RUNNING scan, with ``project.latest_scan_id``
pointing at the newest (non-succeeded) attempt — and assert that:

  * the overview risk / severity / license / component counts are NON-empty,
  * the vuln list + counts return the succeeded scan's rows,
  * the component list returns the succeeded scan's rows,
  * the license list + distribution return the succeeded scan's rows,
  * the obligation list returns the succeeded scan's rows,
  * the build gate evaluates the SAME scan (the consistency invariant), and
  * ``services.scan_resolution.latest_succeeded_scan_id`` and the policy_gate
    re-export resolve to the earlier succeeded scan (never the failed/running one).

The tests run against the real Postgres (CLAUDE.md core rule #1) because the
aggregations depend on live ENUM / CASE behaviour — mocking would test the mock.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_team,
    make_user,
    principal_for,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip latest-succeeded anchoring tests")
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
            "alembic upgrade head failed; anchoring tests cannot run\n"
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
# Local factories (inline per the repo convention; no shared component/finding
# helpers exist). Each uses unique slugs so re-runs against the persistent dev
# DB never collide on uq_components_purl / uq_licenses_spdx_id.
# ---------------------------------------------------------------------------


async def _make_scan_at(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    status: str,
    created_at: datetime,
):
    """A scan row with an explicit ``created_at`` so ordering is deterministic."""
    from models import Scan

    scan = Scan(
        project_id=project_id,
        kind="source",
        status=status,
        progress_percent=100 if status == "succeeded" else 0,
        scan_metadata={},
        created_at=created_at,
        completed_at=created_at if status == "succeeded" else None,
    )
    session.add(scan)
    await session.commit()
    await session.refresh(scan)
    return scan


async def _make_component_version(session: AsyncSession, *, name: str | None = None):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    cname = name or f"anchor-pkg-{suffix}"
    purl = f"pkg:npm/{cname}"
    component = Component(purl=purl, package_type="npm", name=cname)
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
    return component, cv


async def _attach_component(session: AsyncSession, *, scan_id: uuid.UUID, cv_id: uuid.UUID):
    from models import ScanComponent

    sc = ScanComponent(
        scan_id=scan_id,
        component_version_id=cv_id,
        direct=True,
        depth=1,
        raw_data={},
    )
    session.add(sc)
    await session.commit()


async def _make_license(session: AsyncSession, *, spdx_id: str, category: str):
    from sqlalchemy import select

    from models import License

    existing = await session.scalar(select(License).where(License.spdx_id == spdx_id))
    if existing is not None:
        return existing
    lic = License(spdx_id=spdx_id, name=f"{spdx_id} license", category=category)
    session.add(lic)
    await session.commit()
    await session.refresh(lic)
    return lic


async def _attach_license_finding(
    session: AsyncSession, *, scan_id: uuid.UUID, cv_id: uuid.UUID, license_id: uuid.UUID
):
    from models import LicenseFinding

    lf = LicenseFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        license_id=license_id,
        kind="concluded",
        source_path=f"path-{unique_suffix()}",
    )
    session.add(lf)
    await session.commit()


async def _make_obligation(session: AsyncSession, *, license_id: uuid.UUID):
    from models import Obligation

    ob = Obligation(
        license_id=license_id,
        kind="attribution",
        text=f"obligation {unique_suffix()}",
        link=None,
    )
    session.add(ob)
    await session.commit()
    await session.refresh(ob)
    return ob


async def _make_vulnerability(session: AsyncSession, *, severity: str):
    from models import Vulnerability

    v = Vulnerability(
        external_id=f"CVE-2024-{unique_suffix()}",
        source="NVD",
        severity=severity,
        summary=f"{severity} finding {unique_suffix()}",
    )
    session.add(v)
    await session.commit()
    await session.refresh(v)
    return v


async def _attach_vuln_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vulnerability_id: uuid.UUID,
    status: str = "new",
):
    from models import VulnerabilityFinding

    vf = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv_id,
        vulnerability_id=vulnerability_id,
        status=status,
        analysis_state=status,
    )
    session.add(vf)
    await session.commit()


async def _ci_vulns_like_project(db_session: AsyncSession):
    """Build the ``ci-vulns`` shape.

    Earlier SUCCEEDED scan with findings (incl. critical + forbidden license +
    an obligation), then a LATER FAILED scan and a LATER RUNNING scan. The
    denormalized ``project.latest_scan_id`` points at the newest (running)
    attempt — exactly the production state that blanked the project view.

    Returns ``(team, user, project, succeeded_scan, failed_scan, running_scan,
    n_critical)``.
    """
    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)

    base = datetime.now(tz=UTC) - timedelta(hours=3)
    succeeded = await _make_scan_at(
        db_session, project_id=project.id, status="succeeded", created_at=base
    )
    failed = await _make_scan_at(
        db_session,
        project_id=project.id,
        status="failed",
        created_at=base + timedelta(hours=1),
    )
    running = await _make_scan_at(
        db_session,
        project_id=project.id,
        status="running",
        created_at=base + timedelta(hours=2),
    )

    # The denormalized pointer tracks the LAST ATTEMPT (the running scan) — this
    # is what the buggy readers anchored on and what made the view go empty.
    project.latest_scan_id = running.id
    project.updated_at = datetime.now(tz=UTC)
    await db_session.commit()
    await db_session.refresh(project)

    # Two components on the SUCCEEDED scan: one critical-CVE + forbidden-license,
    # one high-CVE + allowed-license.
    _, cv_crit = await _make_component_version(db_session)
    _, cv_high = await _make_component_version(db_session)
    await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv_crit.id)
    await _attach_component(db_session, scan_id=succeeded.id, cv_id=cv_high.id)

    crit = await _make_vulnerability(db_session, severity="critical")
    high = await _make_vulnerability(db_session, severity="high")
    await _attach_vuln_finding(
        db_session, scan_id=succeeded.id, cv_id=cv_crit.id, vulnerability_id=crit.id
    )
    await _attach_vuln_finding(
        db_session, scan_id=succeeded.id, cv_id=cv_high.id, vulnerability_id=high.id
    )

    forbidden = await _make_license(
        db_session, spdx_id=f"GPL-3.0-anchor-{unique_suffix()}", category="forbidden"
    )
    allowed = await _make_license(
        db_session, spdx_id=f"MIT-anchor-{unique_suffix()}", category="allowed"
    )
    await _attach_license_finding(
        db_session, scan_id=succeeded.id, cv_id=cv_crit.id, license_id=forbidden.id
    )
    await _attach_license_finding(
        db_session, scan_id=succeeded.id, cv_id=cv_high.id, license_id=allowed.id
    )
    await _make_obligation(db_session, license_id=forbidden.id)

    return team, user, project, succeeded, failed, running, 1


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


async def test_resolver_picks_earlier_succeeded_over_later_failed_and_running(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import _latest_succeeded_scan_id
    from services.scan_resolution import latest_succeeded_scan_id

    (_team, _user, project, succeeded, _failed, running, _n) = await _ci_vulns_like_project(
        db_session
    )

    resolved = await latest_succeeded_scan_id(db_session, project.id)
    assert resolved == succeeded.id
    assert resolved != project.latest_scan_id  # the pointer is the running scan
    assert resolved != running.id

    # policy_gate must share the resolver (no second copy that could drift).
    assert _latest_succeeded_scan_id is latest_succeeded_scan_id
    assert await _latest_succeeded_scan_id(db_session, project.id) == succeeded.id


async def test_resolver_returns_none_when_no_succeeded_scan(
    db_session: AsyncSession,
) -> None:
    from services.scan_resolution import latest_succeeded_scan_id

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)
    base = datetime.now(tz=UTC) - timedelta(hours=1)
    failed = await _make_scan_at(
        db_session, project_id=project.id, status="failed", created_at=base
    )
    project.latest_scan_id = failed.id
    await db_session.commit()

    assert await latest_succeeded_scan_id(db_session, project.id) is None


# ---------------------------------------------------------------------------
# Overview / Components / Vulnerabilities / Licenses / Obligations + gate
# all reflect the SUCCEEDED scan (the consistency invariant).
# ---------------------------------------------------------------------------


async def test_all_detail_reads_reflect_succeeded_scan_not_failed_attempt(
    db_session: AsyncSession,
) -> None:
    from services.license_service import list_project_licenses
    from services.obligation_service import list_project_obligations
    from services.policy_gate import evaluate_gate
    from services.project_detail_service import (
        get_project_overview,
        list_components_for_project,
    )
    from services.vulnerability_service import list_project_vulnerabilities

    (team, user, project, succeeded, _failed, _running, _n) = await _ci_vulns_like_project(
        db_session
    )
    actor = principal_for(user, team_ids=[team.id], role="developer")

    # --- Overview: NON-empty risk + severity + license + component counts ---
    overview = await get_project_overview(db_session, project_id=project.id, actor=actor)
    assert overview["total_components"] == 2
    assert overview["severity_distribution"]["critical"] == 1
    assert overview["severity_distribution"]["high"] == 1
    assert overview["license_distribution"]["forbidden"] == 1
    assert overview["license_distribution"]["allowed"] == 1
    # Security 1 critical → 80.0, License 1 forbidden → 80.0; overall max = 80.0 —
    # emphatically NOT 0.
    assert overview["risk_score"] == 80.0

    # --- Build gate: the reference the whole view must match ---
    gate = await evaluate_gate(db_session, project.id)
    assert gate.scan_id == succeeded.id
    assert gate.gate == "fail"
    assert gate.critical_cve_count == 1
    assert gate.forbidden_license_count == 1

    # --- Components tab: returns the succeeded scan's rows ---
    components, total_components = await list_components_for_project(
        db_session, project_id=project.id, actor=actor
    )
    assert total_components == 2
    sev_by_count = sorted(c["severity_max"] for c in components)
    assert sev_by_count == ["critical", "high"]

    # --- Vulnerabilities tab: list + counts ---
    vulns, total_vulns = await list_project_vulnerabilities(
        db_session, project_id=project.id, actor=actor
    )
    assert total_vulns == 2
    assert {v["severity"] for v in vulns} == {"critical", "high"}
    # The critical the overview + gate both reported is present in the list.
    assert sum(1 for v in vulns if v["severity"] == "critical") == gate.critical_cve_count

    # --- Licenses tab: list + distribution ---
    lic_items, lic_distribution, lic_total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor
    )
    assert lic_total == 2
    assert lic_distribution["forbidden"] == 1
    assert lic_distribution["allowed"] == 1
    assert lic_distribution["forbidden"] == gate.forbidden_license_count

    # --- Obligations tab: the forbidden license's obligation surfaces ---
    ob_items, _ob_distribution, ob_total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor
    )
    assert ob_total >= 1
    assert any(o["license_category"] == "forbidden" for o in ob_items)


async def test_reads_stay_empty_when_no_succeeded_scan_exists(
    db_session: AsyncSession,
) -> None:
    """A project whose ONLY scan failed still returns the empty 200 shapes
    (never 404/500) — the pre-fix no-scan behaviour is preserved."""
    from services.license_service import list_project_licenses
    from services.obligation_service import list_project_obligations
    from services.policy_gate import evaluate_gate
    from services.project_detail_service import (
        get_project_overview,
        list_components_for_project,
    )
    from services.vulnerability_service import list_project_vulnerabilities

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    base = datetime.now(tz=UTC) - timedelta(hours=1)
    failed = await _make_scan_at(
        db_session, project_id=project.id, status="failed", created_at=base
    )
    project.latest_scan_id = failed.id
    await db_session.commit()
    actor = principal_for(user, team_ids=[team.id], role="developer")

    overview = await get_project_overview(db_session, project_id=project.id, actor=actor)
    assert overview["total_components"] == 0
    assert overview["risk_score"] == 0.0
    # #29: even with NO succeeded snapshot the attempt is surfaced in
    # recent_scans (the table is project-wide, not gated on a resolved snapshot)
    # so the user can still see / re-open it. ``last_succeeded_scan_at`` stays
    # None because there is no succeeded scan to anchor the SBOM label on.
    assert [s.id for s in overview["recent_scans"]] == [failed.id]
    assert overview["last_scan_at"] is not None
    assert overview["last_succeeded_scan_at"] is None

    components, total_components = await list_components_for_project(
        db_session, project_id=project.id, actor=actor
    )
    assert (components, total_components) == ([], 0)

    vulns, total_vulns = await list_project_vulnerabilities(
        db_session, project_id=project.id, actor=actor
    )
    assert (vulns, total_vulns) == ([], 0)

    lic_items, _lic_dist, lic_total = await list_project_licenses(
        db_session, project_id=project.id, actor=actor
    )
    assert (lic_items, lic_total) == ([], 0)

    ob_items, _ob_dist, ob_total = await list_project_obligations(
        db_session, project_id=project.id, actor=actor
    )
    assert (ob_items, ob_total) == ([], 0)

    # The gate also reads "no signal → pass" with no scan id.
    gate = await evaluate_gate(db_session, project.id)
    assert gate.scan_id is None
    assert gate.gate == "pass"


async def test_recent_scans_surface_inflight_scan_without_succeeded_snapshot(
    db_session: AsyncSession,
) -> None:
    """#29: a project whose ONLY scan is still queued/running has no succeeded
    snapshot, but the Overview must still list that in-flight scan so the user
    can track it / re-open the live progress drawer.

    Pre-fix the recent-scans query was nested inside the
    ``if aggregate_scan_id is not None`` block, so a freshly-triggered first scan
    (no succeeded snapshot yet) came back with ``recent_scans == []`` — exactly
    the reported symptom (closed the drawer, nothing in recent scans, no way to
    re-open the running scan).
    """
    from services.project_detail_service import get_project_overview

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    user = await make_user(db_session)
    await make_membership(db_session, user=user, team=team, role="developer")
    project = await make_project(db_session, team=team)
    base = datetime.now(tz=UTC) - timedelta(minutes=5)
    running = await _make_scan_at(
        db_session, project_id=project.id, status="running", created_at=base
    )
    project.latest_scan_id = running.id
    await db_session.commit()
    actor = principal_for(user, team_ids=[team.id], role="developer")

    overview = await get_project_overview(db_session, project_id=project.id, actor=actor)
    # No succeeded snapshot → distributions stay empty …
    assert overview["total_components"] == 0
    assert overview["risk_score"] == 0.0
    assert overview["last_succeeded_scan_at"] is None
    # … but the in-flight scan IS surfaced (the #29 regression guard).
    assert [s.id for s in overview["recent_scans"]] == [running.id]
    assert overview["recent_scans"][0].status == "running"
    assert overview["last_scan_at"] is not None
