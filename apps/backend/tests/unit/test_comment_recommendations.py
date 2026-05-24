"""
DB-backed unit tests for the SCA-comment upgrade recommendations builder —
v2.2 2.2-a3 (``api.v1.policy_gate._build_recommended_upgrades``).

The helper turns a scan's open findings + the recommendation engine into the
prioritized "upgrade to X" rows the PR comment renders. We drive it against the
live Postgres (CLAUDE.md core rule #1 — no SQLite, no mocking our own infra)
with a session bound to ``DATABASE_URL``; skipped when unset.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_organization,
    make_project,
    make_scan,
    make_team,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip comment-recommendation tests")
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
            "alembic upgrade head failed; comment-recommendation tests cannot run\n"
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
# Seed helpers
# ---------------------------------------------------------------------------


async def _make_cv(session: AsyncSession, *, name: str, version: str):
    from models import Component, ComponentVersion

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


async def _make_vuln(session: AsyncSession, *, cve_id: str, severity: str, epss=None):
    from models import Vulnerability

    v = Vulnerability(
        external_id=cve_id,
        source="NVD",
        severity=severity,
        epss_score=epss,
        summary=cve_id,
    )
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


async def _attach_scan_component(
    session: AsyncSession, *, scan_id, cv_id, direct=False, depth=None
):
    from models import ScanComponent

    sc = ScanComponent(
        scan_id=scan_id, component_version_id=cv_id, direct=direct, depth=depth
    )
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


async def _scan(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    project = await make_project(session, team=team)
    return await make_scan(session, project=project, status="succeeded")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_build_recommendations_sorts_direct_critical_first(
    db_session: AsyncSession,
) -> None:
    from api.v1.policy_gate import _build_recommended_upgrades

    scan = await _scan(db_session)
    suffix = unique_suffix()

    # Transitive low-severity component with a fix.
    cv_trans = await _make_cv(db_session, name=f"trans-{suffix}", version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv_trans.id, depth=4)
    v_trans = await _make_vuln(db_session, cve_id=f"CVE-t-{suffix}", severity="low")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv_trans.id, vuln_id=v_trans.id, fixed_version="1.1.0"
    )

    # Direct critical component with a fix → must sort first.
    cv_direct = await _make_cv(db_session, name=f"direct-{suffix}", version="2.0.0")
    await _attach_scan_component(
        db_session, scan_id=scan.id, cv_id=cv_direct.id, direct=True, depth=1
    )
    v_crit = await _make_vuln(
        db_session, cve_id=f"CVE-c-{suffix}", severity="critical", epss="0.95"
    )
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv_direct.id, vuln_id=v_crit.id, fixed_version="2.5.0"
    )

    recs = await _build_recommended_upgrades(db_session, scan_id=scan.id)
    assert len(recs) == 2
    # Direct critical first.
    assert recs[0].component_name == f"direct-{suffix}"
    assert recs[0].recommended_version == "2.5.0"
    assert recs[0].direct is True
    assert recs[0].max_severity == "critical"
    assert f"CVE-c-{suffix}" in recs[0].cve_ids
    # Transitive low second.
    assert recs[1].component_name == f"trans-{suffix}"
    assert recs[1].direct is False


async def test_build_recommendations_skips_non_actionable(
    db_session: AsyncSession,
) -> None:
    """A component with an open finding that has no fix version produces a
    'no_fix_version' recommendation, which the comment omits entirely."""
    from api.v1.policy_gate import _build_recommended_upgrades

    scan = await _scan(db_session)
    suffix = unique_suffix()

    cv = await _make_cv(db_session, name=f"nofix-{suffix}", version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id, direct=True, depth=1)
    v = await _make_vuln(db_session, cve_id=f"CVE-nf-{suffix}", severity="high")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v.id, fixed_version=None
    )

    recs = await _build_recommended_upgrades(db_session, scan_id=scan.id)
    assert recs == ()


async def test_build_recommendations_excludes_dispositioned(
    db_session: AsyncSession,
) -> None:
    """Findings in a closed status (fixed/not_affected/false_positive) don't
    contribute — a fully-dispositioned component yields no recommendation."""
    from api.v1.policy_gate import _build_recommended_upgrades

    scan = await _scan(db_session)
    suffix = unique_suffix()

    cv = await _make_cv(db_session, name=f"disp-{suffix}", version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id, direct=True, depth=1)
    v = await _make_vuln(db_session, cve_id=f"CVE-disp-{suffix}", severity="critical")
    await _attach_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vuln_id=v.id,
        fixed_version="9.9.9",
        status="not_affected",
    )

    recs = await _build_recommended_upgrades(db_session, scan_id=scan.id)
    assert recs == ()


async def test_build_recommendations_groups_multiple_cves_per_component(
    db_session: AsyncSession,
) -> None:
    """Two open CVEs on the same component collapse to one recommendation
    (the semver max of their fixes) listing both CVE ids."""
    from api.v1.policy_gate import _build_recommended_upgrades

    scan = await _scan(db_session)
    suffix = unique_suffix()

    cv = await _make_cv(db_session, name=f"multi-{suffix}", version="1.0.0")
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id, direct=True, depth=1)
    v1 = await _make_vuln(db_session, cve_id=f"CVE-m1-{suffix}", severity="high")
    v2 = await _make_vuln(db_session, cve_id=f"CVE-m2-{suffix}", severity="critical")
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v1.id, fixed_version="1.2.0"
    )
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vuln_id=v2.id, fixed_version="1.4.0"
    )

    recs = await _build_recommended_upgrades(db_session, scan_id=scan.id)
    assert len(recs) == 1
    assert recs[0].recommended_version == "1.4.0"  # max of 1.2.0, 1.4.0
    assert recs[0].max_severity == "critical"
    assert set(recs[0].cve_ids) == {f"CVE-m1-{suffix}", f"CVE-m2-{suffix}"}


async def test_build_recommendations_empty_scan(db_session: AsyncSession) -> None:
    from api.v1.policy_gate import _build_recommended_upgrades

    scan = await _scan(db_session)
    recs = await _build_recommended_upgrades(db_session, scan_id=scan.id)
    assert recs == ()
