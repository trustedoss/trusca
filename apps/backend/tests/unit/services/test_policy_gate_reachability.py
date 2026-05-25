"""
DB-backed unit tests for the reachability surface of
``services/policy_gate.evaluate_gate`` — v2.3 r2.

Two contracts under test:

1. **Surfacing (always on, no behaviour change):** the gate ALWAYS reports
   ``reachable_critical_cve_count`` (the subset of open criticals proven
   reachable). With the opt-in mode OFF, the verdict is byte-for-byte the legacy
   behaviour — an unreachable / not-analysed open critical still FAILS the gate,
   and ``critical_cve_count`` still counts every open critical.

2. **Opt-in reachable-only mode (``GATE_REACHABLE_CRITICAL_ONLY``):** when
   enabled, only reachable open criticals count toward the verdict — an
   operator-chosen relaxation. NULL (not analysed) and FALSE (proven
   unreachable) criticals no longer block.

The no-regression case is the most important one: it proves adding the
reachability signal does NOT change the default gate decision.

Mirrors ``tests/unit/services/test_policy_gate.py``: alembic upgrade head once,
fresh ``AsyncSession`` per test, real Postgres (CLAUDE.md core rule #1 + §2).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
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
        pytest.skip("DATABASE_URL not set — skip policy_gate reachability tests")
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
            "alembic upgrade head failed; policy_gate reachability tests cannot run\n"
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


async def _seed_project_with_team(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    project = await make_project(session, team=team)
    return team, user, project


async def _seed_critical_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    reachable: bool | None,
    status: str = "new",
):
    """Seed a component + critical CVE + finding with the given reachability."""
    from models import (
        Component,
        ComponentVersion,
        ScanComponent,
        Vulnerability,
        VulnerabilityFinding,
    )

    suffix = unique_suffix()
    purl = f"pkg:golang/gate-reach-{suffix}"
    component = Component(purl=purl, package_type="golang", name=f"gate-reach-{suffix}")
    session.add(component)
    await session.commit()
    await session.refresh(component)

    cv = ComponentVersion(
        component_id=component.id, version="1.0.0", purl_with_version=f"{purl}@1.0.0"
    )
    session.add(cv)
    await session.commit()
    await session.refresh(cv)

    session.add(ScanComponent(scan_id=scan_id, component_version_id=cv.id, direct=True))
    await session.commit()

    vuln = Vulnerability(
        external_id=f"CVE-2099-gr-{suffix}",
        source="NVD",
        severity="critical",
        summary=f"gate reach {suffix}",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    finding = VulnerabilityFinding(
        scan_id=scan_id,
        component_version_id=cv.id,
        vulnerability_id=vuln.id,
        status=status,
        analysis_state=status,
        reachable=reachable,
        reachability_source="govulncheck" if reachable is not None else None,
        reachability_analyzed_at=(
            datetime.now(tz=UTC) if reachable is not None else None
        ),
    )
    session.add(finding)
    await session.commit()
    await session.refresh(finding)
    return finding


# ---------------------------------------------------------------------------
# Surfacing + no-regression (default mode OFF)
# ---------------------------------------------------------------------------


async def test_reachable_count_surfaced_without_changing_verdict(
    db_session: AsyncSession, monkeypatch
) -> None:
    """A reachable open critical fails the gate (as always) AND is counted in
    the reachable subset — the verdict is unchanged by the new signal."""
    from services.policy_gate import evaluate_gate

    monkeypatch.delenv("GATE_REACHABLE_CRITICAL_ONLY", raising=False)

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=True)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.critical_cve_count == 1
    assert result.reachable_critical_cve_count == 1
    assert result.reachable_gate_enforced is False


async def test_unreachable_critical_still_blocks_by_default(
    db_session: AsyncSession, monkeypatch
) -> None:
    """NO-REGRESSION: with the opt-in mode OFF, a proven-UNREACHABLE open
    critical still fails the gate exactly as before r2."""
    from services.policy_gate import evaluate_gate

    monkeypatch.delenv("GATE_REACHABLE_CRITICAL_ONLY", raising=False)

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=False)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"  # legacy contract preserved
    assert result.critical_cve_count == 1
    assert result.reachable_critical_cve_count == 0  # not reachable
    assert result.reachable_gate_enforced is False
    assert result.reason is not None
    # Legacy wording (NOT "reachable critical") when the flag is off.
    assert "critical" in result.reason
    assert "reachable critical" not in result.reason


async def test_not_analysed_critical_still_blocks_by_default(
    db_session: AsyncSession, monkeypatch
) -> None:
    """NO-REGRESSION: a NULL-reachability (not analysed) open critical still
    blocks by default — we never down-rank an unanalysed finding."""
    from services.policy_gate import evaluate_gate

    monkeypatch.delenv("GATE_REACHABLE_CRITICAL_ONLY", raising=False)

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=None)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.critical_cve_count == 1
    assert result.reachable_critical_cve_count == 0
    assert result.reachable_gate_enforced is False


async def test_no_scan_path_reports_reachable_defaults(
    db_session: AsyncSession, monkeypatch
) -> None:
    """The no-succeeded-scan fast path returns the reachability fields too."""
    from services.policy_gate import evaluate_gate

    monkeypatch.delenv("GATE_REACHABLE_CRITICAL_ONLY", raising=False)

    _, _, project = await _seed_project_with_team(db_session)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"
    assert result.scan_id is None
    assert result.reachable_critical_cve_count == 0
    assert result.reachable_gate_enforced is False


# ---------------------------------------------------------------------------
# Opt-in reachable-only critical mode (ON)
# ---------------------------------------------------------------------------


async def test_reachable_only_mode_relaxes_unreachable_critical(
    db_session: AsyncSession, monkeypatch
) -> None:
    """With GATE_REACHABLE_CRITICAL_ONLY=1, a proven-unreachable open critical
    no longer blocks — the verdict relaxes to pass.

    NB: this scan IS reachability-analysed (one finding with reachable=False is a
    non-NULL verdict), so the safe-by-default fallback does NOT kick in and the
    relaxation legitimately applies, suppressing the proven-unreachable critical.
    """
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_REACHABLE_CRITICAL_ONLY", "1")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=False)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "pass"  # relaxed: proven-unreachable critical doesn't block
    assert result.critical_cve_count == 0  # blocking count excludes the unreachable
    assert result.reachable_critical_cve_count == 0
    assert result.reachable_gate_enforced is True
    assert result.reachable_relaxation_applied is True  # scan was analysed


async def test_reachable_only_mode_still_blocks_reachable_critical(
    db_session: AsyncSession, monkeypatch
) -> None:
    """With the opt-in mode ON, a REACHABLE open critical still fails the gate
    and the reason text marks it as a reachable critical."""
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_REACHABLE_CRITICAL_ONLY", "true")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=True)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    assert result.critical_cve_count == 1
    assert result.reachable_critical_cve_count == 1
    assert result.reachable_gate_enforced is True
    assert result.reason is not None
    assert "reachable critical" in result.reason


# ---------------------------------------------------------------------------
# Safe-by-default fallback (security-reviewer fix-first, Medium #1)
# ---------------------------------------------------------------------------


async def test_reachable_only_mode_safe_fallback_all_null_still_blocks(
    db_session: AsyncSession, monkeypatch
) -> None:
    """FALLBACK (a): mode ON + EVERY open critical is NULL-reachability
    (un-analysed — e.g. a non-Go ecosystem). The relaxation must NOT apply, so
    the gate still FAILS on the full set of open criticals — the flag does NOT
    silently disable the gate for ecosystems reachability never analysed."""
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_REACHABLE_CRITICAL_ONLY", "on")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=None)
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=None)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"  # safe fallback: NOT relaxed
    assert result.critical_cve_count == 2  # full open-critical population blocks
    assert result.reachable_critical_cve_count == 0
    assert result.reachable_gate_enforced is True  # flag was set
    assert result.reachable_relaxation_applied is False  # but it had no effect
    assert result.reason is not None
    # Legacy wording — the relaxation did not take effect, so don't claim
    # "reachable critical".
    assert "reachable critical" not in result.reason


async def test_reachable_only_mode_excludes_only_analysed_unreachable(
    db_session: AsyncSession, monkeypatch
) -> None:
    """FALLBACK (b): mode ON + a mix of analysed findings. With at least one
    non-NULL verdict the relaxation applies and excludes ONLY the proven-
    unreachable (reachable=False) finding; the reachable one still blocks."""
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_REACHABLE_CRITICAL_ONLY", "1")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=True)
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=False)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"  # the reachable critical still blocks
    assert result.critical_cve_count == 1  # total(2) - unreachable(1)
    assert result.reachable_critical_cve_count == 1
    assert result.reachable_gate_enforced is True
    assert result.reachable_relaxation_applied is True


async def test_reachable_only_mode_keeps_null_blocking_when_analysed(
    db_session: AsyncSession, monkeypatch
) -> None:
    """FALLBACK (c): mode ON + a NULL critical mixed with a reachable=True one.
    The scan IS analysed (the TRUE finding), so the relaxation applies — but a
    NULL (not analysed) critical is NEVER treated as unreachable: it stays
    blocking alongside the reachable one. Only reachable=False is excluded."""
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_REACHABLE_CRITICAL_ONLY", "yes")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=True)
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=None)

    result = await evaluate_gate(db_session, project.id)

    assert result.gate == "fail"
    # total(2) - unreachable(0) == 2: TRUE blocks, NULL kept conservatively.
    assert result.critical_cve_count == 2
    assert result.reachable_critical_cve_count == 1  # only the TRUE one
    assert result.reachable_gate_enforced is True
    assert result.reachable_relaxation_applied is True


async def test_reachable_only_mode_relaxation_emits_warning(
    db_session: AsyncSession, monkeypatch
) -> None:
    """When the relaxation actually suppresses a blocking critical, a WARNING
    (not INFO) structured event is emitted.

    structlog is configured with a ``PrintLoggerFactory`` (stdout, not stdlib),
    so we capture events with ``structlog.testing.capture_logs`` rather than
    pytest's ``caplog``.
    """
    from structlog.testing import capture_logs

    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_REACHABLE_CRITICAL_ONLY", "1")

    _, _, project = await _seed_project_with_team(db_session)
    scan = await make_scan(db_session, project=project, status="succeeded")
    # One reachable (keeps blocking) + one unreachable (suppressed) → relaxation
    # both applies AND suppresses, so a WARNING must fire.
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=True)
    await _seed_critical_finding(db_session, scan_id=scan.id, reachable=False)

    with capture_logs() as events:
        await evaluate_gate(db_session, project.id)

    suppressed = [
        e
        for e in events
        if e.get("event") == "policy_gate.reachable_relaxation_suppressed_criticals"
    ]
    assert len(suppressed) == 1
    assert suppressed[0]["log_level"] == "warning"
    assert suppressed[0]["all_critical_cve_count"] == 2
    assert suppressed[0]["blocking_critical_count"] == 1
    assert suppressed[0]["unreachable_critical_count"] == 1


# ---------------------------------------------------------------------------
# Flag parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("nonsense", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("Yes", True),
        ("on", True),
    ],
)
def test_resolve_reachable_critical_only(monkeypatch, env_value, expected) -> None:
    from services.policy_gate import _resolve_reachable_critical_only

    if env_value is None:
        monkeypatch.delenv("GATE_REACHABLE_CRITICAL_ONLY", raising=False)
    else:
        monkeypatch.setenv("GATE_REACHABLE_CRITICAL_ONLY", env_value)

    assert _resolve_reachable_critical_only() is expected
