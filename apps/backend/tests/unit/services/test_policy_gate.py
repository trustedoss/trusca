"""
DB-backed unit tests for ``services/policy_gate.py`` — Phase 5 PR #17.

The service is a pure read against the live Postgres (no auth), so the
tests drive ``evaluate_gate`` directly with a session bound to the
configured ``DATABASE_URL``. We mirror the layout of
``tests/unit/test_project_detail_service.py``: ``alembic upgrade head``
once per module, fresh ``AsyncSession`` per test, no mocking of our own
infra (CLAUDE.md core rule #1 + §2 testing rules).

Covered cases (5):

1. Project with no scans at all                           → gate=pass, scan_id=None
2. Project whose latest succeeded scan has no findings    → gate=pass
3. Project with one open critical CVE                     → gate=fail
4. Project with one forbidden license                     → gate=fail
5. The latest succeeded scan is preferred over a more
   recent failed scan (regression for the
   ``Project.latest_scan_id`` shortcut we deliberately
   avoid)                                                  → gate=pass against
                                                              the older succeeded scan
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests._helpers import (
    make_membership,
    make_organization,
    make_project,
    make_scan,
    make_team,
    make_user,
    unique_suffix,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent

pytestmark = pytest.mark.integration


def _require_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set — skip policy_gate service tests")
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
            f"alembic upgrade head failed; policy_gate service tests cannot run\n"
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
# Local seed helpers
# ---------------------------------------------------------------------------


async def _seed_project_with_team(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    return team, user, project


async def _make_component_version(session: AsyncSession):
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    purl = f"pkg:npm/policy-gate-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"policy-gate-{suffix}")
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


async def _attach_scan_component(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
):
    from models import ScanComponent

    sc = ScanComponent(scan_id=scan_id, component_version_id=cv_id, direct=True)
    session.add(sc)
    await session.commit()
    await session.refresh(sc)
    return sc


async def _make_vulnerability(session: AsyncSession, *, severity: str):
    from models import Vulnerability

    suffix = unique_suffix()
    v = Vulnerability(
        external_id=f"CVE-2099-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"Test vuln {suffix}",
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
    )
    session.add(vf)
    await session.commit()
    await session.refresh(vf)
    return vf


async def _resolve_or_make_license(
    session: AsyncSession,
    *,
    spdx_id: str,
    category: str,
):
    from models import License

    existing = await session.scalar(select(License).where(License.spdx_id == spdx_id))
    if existing is not None:
        return existing
    licence = License(spdx_id=spdx_id, name=spdx_id, category=category)
    session.add(licence)
    await session.commit()
    await session.refresh(licence)
    return licence


async def _attach_license_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    license_id: uuid.UUID,
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
    await session.refresh(lf)
    return lf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_evaluate_gate_no_scan_returns_pass_with_null_scan_id(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project_with_team(db_session)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.reason is None
    assert result.scan_id is None
    assert result.critical_cve_count == 0
    assert result.forbidden_license_count == 0
    assert result.project_id == project.id
    assert isinstance(result.evaluated_at, datetime)


async def test_evaluate_gate_succeeded_scan_with_no_findings_passes(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.reason is None
    assert result.scan_id == scan.id
    assert result.critical_cve_count == 0
    assert result.forbidden_license_count == 0


async def test_evaluate_gate_open_critical_cve_fails(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")

    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)

    vuln = await _make_vulnerability(db_session, severity="critical")
    await _attach_vuln_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vulnerability_id=vuln.id,
        status="new",
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.scan_id == scan.id
    assert result.critical_cve_count == 1
    assert result.forbidden_license_count == 0
    assert result.reason is not None
    assert "critical" in result.reason


async def test_evaluate_gate_closed_critical_cve_does_not_block(
    db_session: AsyncSession,
) -> None:
    """Critical CVE in not_affected/fixed/false_positive must not fail the gate."""
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")

    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)

    vuln = await _make_vulnerability(db_session, severity="critical")
    await _attach_vuln_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv.id,
        vulnerability_id=vuln.id,
        status="not_affected",
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.critical_cve_count == 0


async def test_evaluate_gate_forbidden_license_fails(
    db_session: AsyncSession,
) -> None:
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")

    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)

    licence = await _resolve_or_make_license(
        db_session, spdx_id="GPL-3.0-only", category="forbidden"
    )
    await _attach_license_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, license_id=licence.id
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.scan_id == scan.id
    assert result.critical_cve_count == 0
    assert result.forbidden_license_count == 1
    assert result.reason is not None
    assert "forbidden" in result.reason


async def test_evaluate_gate_uses_latest_succeeded_scan_not_latest_overall(
    db_session: AsyncSession,
) -> None:
    """A more recent FAILED scan must not displace the older succeeded one."""
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project_with_team(db_session)

    older = await make_scan(db_session, project=project, status="succeeded")
    # `make_scan` sets `created_at = now()`. Backdate the older scan so the
    # ORDER BY created_at DESC produces a deterministic outcome.
    older.created_at = datetime.now(tz=UTC) - timedelta(minutes=5)
    await db_session.commit()
    await db_session.refresh(older)

    # Newer FAILED scan — must be ignored by the gate.
    newer = await make_scan(db_session, project=project, status="succeeded")
    newer.status = "failed"
    await db_session.commit()
    await db_session.refresh(newer)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    # The verdict must reflect the OLDER succeeded scan.
    assert result.scan_id == older.id


async def test_evaluate_gate_distinct_components_for_forbidden_count(
    db_session: AsyncSession,
) -> None:
    """Multiple license_findings on the same cv collapse to one in the count."""
    from services.policy_gate import evaluate_gate

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")

    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)

    licence = await _resolve_or_make_license(
        db_session, spdx_id="AGPL-3.0-only", category="forbidden"
    )
    # Two findings (declared + concluded) on the SAME cv. The count should
    # collapse to 1 because we count distinct component_versions.
    from models import LicenseFinding

    db_session.add(
        LicenseFinding(
            scan_id=scan.id,
            component_version_id=cv.id,
            license_id=licence.id,
            kind="declared",
            source_path=f"a-{unique_suffix()}",
        )
    )
    db_session.add(
        LicenseFinding(
            scan_id=scan.id,
            component_version_id=cv.id,
            license_id=licence.id,
            kind="concluded",
            source_path=f"b-{unique_suffix()}",
        )
    )
    await db_session.commit()

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.forbidden_license_count == 1
