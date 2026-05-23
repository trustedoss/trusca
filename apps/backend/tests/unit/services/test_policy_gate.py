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
from decimal import Decimal
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


async def _make_vulnerability(
    session: AsyncSession, *, severity: str, epss_score: Decimal | None = None
):
    from models import Vulnerability

    suffix = unique_suffix()
    v = Vulnerability(
        external_id=f"CVE-2099-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"Test vuln {suffix}",
        # Set at INSERT (not via a post-create UPDATE) so the audit listener
        # never captures a Decimal in a JSONB diff.
        epss_score=epss_score,
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


# ---------------------------------------------------------------------------
# EPSS gate option (v2.1) — env-driven, opt-in. Pure unit cases (no DB) for
# the threshold parser + reason builder, plus DB-backed cases for the count.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, None),  # unset → disabled
        ("", None),  # empty → disabled
        ("   ", None),  # whitespace → disabled
        ("not-a-float", None),  # unparseable → disabled (fail-safe)
        ("-0.1", None),  # below range → disabled
        ("1.5", None),  # above range → disabled
        ("0", 0.0),
        ("1", 1.0),
        ("0.5", 0.5),
        ("0.97123", 0.97123),
    ],
)
def test_resolve_epss_threshold(monkeypatch, env_value, expected) -> None:
    """Env parse: only a float in [0, 1] enables the gate; everything else
    fails safe to disabled (None) and never relaxes the gate."""
    from services.policy_gate import _resolve_epss_threshold

    if env_value is None:
        monkeypatch.delenv("GATE_EPSS_THRESHOLD", raising=False)
    else:
        monkeypatch.setenv("GATE_EPSS_THRESHOLD", env_value)

    assert _resolve_epss_threshold() == expected


def test_build_reason_omits_epss_when_disabled() -> None:
    """A None threshold (gate disabled) must not append an EPSS clause even if
    a count is somehow passed — legacy reason text stays byte-for-byte."""
    from services.policy_gate import _build_reason

    assert _build_reason(0, 0, 5, None) is None
    assert _build_reason(1, 0, 5, None) == "1 critical CVE detected"


def test_build_reason_appends_epss_clause_when_active() -> None:
    from services.policy_gate import _build_reason

    reason = _build_reason(0, 0, 3, 0.5)
    assert reason == "3 open CVEs with EPSS >= 0.5"
    # Singular form + combined with the critical clause.
    combined = _build_reason(1, 0, 1, 0.9)
    assert combined == "1 critical CVE detected; 1 open CVE with EPSS >= 0.9"


async def test_evaluate_gate_epss_disabled_preserves_legacy_behaviour(
    db_session: AsyncSession, monkeypatch
) -> None:
    """With GATE_EPSS_THRESHOLD unset, a high-EPSS open CVE does NOT fail the
    gate — the legacy critical/forbidden contract is 100% preserved."""
    from services.policy_gate import evaluate_gate

    monkeypatch.delenv("GATE_EPSS_THRESHOLD", raising=False)

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)

    # A non-critical CVE with a very high EPSS score.
    vuln = await _make_vulnerability(
        db_session, severity="medium", epss_score=Decimal("0.99000")
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=vuln.id, status="new"
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.epss_gate_count == 0
    assert result.epss_threshold is None


async def test_evaluate_gate_epss_threshold_fails_on_high_epss(
    db_session: AsyncSession, monkeypatch
) -> None:
    """With the threshold set, an open finding whose CVE EPSS >= threshold
    fails the gate even when severity is not critical."""
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_EPSS_THRESHOLD", "0.5")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)

    vuln = await _make_vulnerability(
        db_session, severity="medium", epss_score=Decimal("0.80000")
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=vuln.id, status="new"
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.epss_gate_count == 1
    assert result.epss_threshold == 0.5
    assert result.critical_cve_count == 0
    assert result.reason is not None
    assert "EPSS" in result.reason


async def test_evaluate_gate_epss_excludes_null_and_closed(
    db_session: AsyncSession, monkeypatch
) -> None:
    """NULL EPSS and dispositioned (closed) findings do not trip the EPSS gate."""
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_EPSS_THRESHOLD", "0.5")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")

    # (a) NULL epss — must be excluded.
    _, cv_null = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv_null.id)
    v_null = await _make_vulnerability(db_session, severity="high")  # epss_score stays None
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv_null.id, vulnerability_id=v_null.id, status="new"
    )

    # (b) high epss but CLOSED finding — must be excluded.
    _, cv_closed = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv_closed.id)
    v_closed = await _make_vulnerability(
        db_session, severity="high", epss_score=Decimal("0.90000")
    )
    await _attach_vuln_finding(
        db_session,
        scan_id=scan.id,
        cv_id=cv_closed.id,
        vulnerability_id=v_closed.id,
        status="not_affected",
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.epss_gate_count == 0
    assert result.epss_threshold == 0.5


async def test_evaluate_gate_epss_below_threshold_passes(
    db_session: AsyncSession, monkeypatch
) -> None:
    """An open finding with EPSS strictly below the threshold passes."""
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_EPSS_THRESHOLD", "0.5")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    vuln = await _make_vulnerability(
        db_session, severity="low", epss_score=Decimal("0.40000")
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=vuln.id, status="new"
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.epss_gate_count == 0


@pytest.mark.parametrize(
    ("epss_score", "expected_gate", "expected_count"),
    [
        # security-reviewer Low #1 — pin the `>=` boundary of
        # `_count_open_epss_findings`. The threshold is GATE_EPSS_THRESHOLD=0.5;
        # the column is Numeric(6, 5) so both values below are exactly
        # representable (no float-rounding fuzz on the boundary).
        #
        #   score == threshold (0.50000)  → counted (>= is inclusive)  → FAIL
        #   score just-below   (0.49999)  → NOT counted (strictly <)   → PASS
        #
        # These two cases are the contract that distinguishes `>=` from `>`:
        # a `>` regression would flip the equality row to pass, and a `>=`-but-
        # off-by-an-epsilon bug would flip the just-below row to fail. Pinning
        # both directions fences the comparator on the exact boundary.
        pytest.param(Decimal("0.50000"), "fail", 1, id="equal_to_threshold_counts"),
        pytest.param(Decimal("0.49999"), "pass", 0, id="just_below_threshold_excluded"),
    ],
)
async def test_evaluate_gate_epss_ge_boundary(
    db_session: AsyncSession,
    monkeypatch,
    epss_score: Decimal,
    expected_gate: str,
    expected_count: int,
) -> None:
    """Pin the inclusive `>=` semantics of the EPSS gate at the exact boundary.

    An open finding whose CVE EPSS equals the threshold MUST count (the gate
    fails); one a single representable step below MUST NOT (the gate passes).
    This is the boundary the existing fail/pass/disabled/NULL/closed cases
    leave un-fenced — they all sit comfortably away from the threshold.
    """
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_EPSS_THRESHOLD", "0.5")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    _, cv = await _make_component_version(db_session)
    await _attach_scan_component(db_session, scan_id=scan.id, cv_id=cv.id)
    vuln = await _make_vulnerability(
        db_session, severity="medium", epss_score=epss_score
    )
    await _attach_vuln_finding(
        db_session, scan_id=scan.id, cv_id=cv.id, vulnerability_id=vuln.id, status="new"
    )

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == expected_gate
    assert result.epss_gate_count == expected_count
    assert result.epss_threshold == 0.5
    # The boundary must not be reached through the critical/forbidden paths —
    # the seeded finding is `medium` severity with no license, so any failure
    # here is attributable to the EPSS comparator alone.
    assert result.critical_cve_count == 0
    assert result.forbidden_license_count == 0
