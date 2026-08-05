"""The action queue's blocking rule must agree with ``evaluate_gate``.

Why this test carries the design
--------------------------------
``services/action_queue_service.py`` deliberately does not call
:func:`services.policy_gate.evaluate_gate`. Doing so would cost five to seven
queries per project, and the alternatives — stamping the verdict on the scan
row, or caching it — go stale the moment someone triages a finding or edits a
licence policy. So the queue aggregates the gate's *inputs* across the whole
portfolio in two grouped queries and applies the same threshold itself.

That leaves the blocking rule living in two places, which CLAUDE.md hardening
rule #2 identifies as the shape defects hide in: each side stays green on its
own while they quietly diverge. This test is the price of the design. It runs
both paths over the same rows and fails on any disagreement, so a change to
one that is not mirrored in the other cannot merge.

The matrix is deliberately about *disagreement-prone* states rather than happy
paths: a finding that is open, one that is dismissed, a component with two
forbidden licences (the queue counts distinct components, and a naive row
count would double it), and a project whose newest scan failed after an older
one succeeded.
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
        pytest.skip("DATABASE_URL not set — skip action-queue parity tests")
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
        pytest.skip(f"alembic upgrade head failed:\n{result.stderr}")


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


# --- fixtures -------------------------------------------------------------


async def _component_version(session: AsyncSession) -> uuid.UUID:
    from models import Component, ComponentVersion

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
    return cv.id


async def _critical_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    status: str = "new",
) -> None:
    from models import ScanComponent, Vulnerability, VulnerabilityFinding

    cv_id = await _component_version(session)
    session.add(
        ScanComponent(
            scan_id=scan_id, component_version_id=cv_id, direct=True, raw_data={}
        )
    )
    vuln = Vulnerability(
        external_id=f"CVE-2024-{unique_suffix()}",
        source="NVD",
        severity="critical",
        summary="parity fixture",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv_id,
            vulnerability_id=vuln.id,
            status=status,
        )
    )
    await session.commit()


async def _forbidden_component(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    license_count: int = 1,
) -> None:
    """One component carrying ``license_count`` forbidden licences.

    With two, a row count and a distinct-component count diverge — which is
    the point: the gate counts components, so the queue must too.
    """
    from models import License, LicenseFinding, ScanComponent

    cv_id = await _component_version(session)
    session.add(
        ScanComponent(
            scan_id=scan_id, component_version_id=cv_id, direct=True, raw_data={}
        )
    )
    await session.commit()

    for _ in range(license_count):
        suffix = unique_suffix()
        lic = License(
            spdx_id=f"LicenseRef-{suffix}",
            name=f"Forbidden {suffix}",
            category="forbidden",
        )
        session.add(lic)
        await session.commit()
        await session.refresh(lic)

        session.add(
            LicenseFinding(
                scan_id=scan_id,
                component_version_id=cv_id,
                license_id=lic.id,
                kind="concluded",
                source_path=f"path-{suffix}",
            )
        )
    await session.commit()


async def _project_with_scan(
    session: AsyncSession, *, team, status: str = "succeeded"
) -> tuple[uuid.UUID, uuid.UUID]:
    project = await make_project(session, team=team)
    scan = await make_scan(session, project=project, status=status)
    return project.id, scan.id


# --- the contract ---------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "critical_open", "critical_dismissed", "forbidden_licences"),
    [
        ("clean", 0, 0, 0),
        ("one open critical", 1, 0, 0),
        ("only dismissed criticals", 0, 2, 0),
        ("one forbidden component", 0, 0, 1),
        # Two licences on ONE component: distinct-component counting is the
        # difference between agreeing with the gate and reporting double.
        ("one component, two forbidden licences", 0, 0, 2),
        ("both signals", 2, 1, 1),
    ],
)
async def test_queue_blocking_matches_evaluate_gate(
    db_session: AsyncSession,
    label: str,
    critical_open: int,
    critical_dismissed: int,
    forbidden_licences: int,
) -> None:
    from services.action_queue_service import _blocked_for_projects
    from services.policy_gate import evaluate_gate

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project_id, scan_id = await _project_with_scan(db_session, team=team)

    for _ in range(critical_open):
        await _critical_finding(db_session, scan_id=scan_id)
    for _ in range(critical_dismissed):
        await _critical_finding(db_session, scan_id=scan_id, status="not_affected")
    if forbidden_licences:
        await _forbidden_component(
            db_session, scan_id=scan_id, license_count=forbidden_licences
        )

    verdict = await evaluate_gate(db_session, project_id)
    blocked = await _blocked_for_projects(db_session, project_ids=[project_id])

    assert (verdict.gate == "fail") == bool(blocked), (
        f"{label}: evaluate_gate said {verdict.gate!r} but the queue "
        f"{'listed' if blocked else 'did not list'} the project"
    )

    if blocked:
        entry = blocked[0]
        assert entry.critical_cve_count == verdict.critical_cve_count, label
        assert entry.forbidden_license_count == verdict.forbidden_license_count, label


async def test_queue_reads_the_same_scan_the_gate_does(
    db_session: AsyncSession,
) -> None:
    """A newer failed scan must not shadow the latest succeeded one.

    Both paths resolve "latest succeeded", but through different helpers, so
    the agreement is worth asserting rather than assuming.
    """
    from services.action_queue_service import _blocked_for_projects
    from services.policy_gate import evaluate_gate

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    older = await make_scan(db_session, project=project, status="succeeded")
    await _critical_finding(db_session, scan_id=older.id)
    # A later scan that failed carries no findings; if either side picked it
    # up, the project would read as clean.
    await make_scan(db_session, project=project, status="failed")

    verdict = await evaluate_gate(db_session, project.id)
    blocked = await _blocked_for_projects(db_session, project_ids=[project.id])

    assert verdict.gate == "fail"
    assert len(blocked) == 1
    assert blocked[0].scan_id == older.id
    assert blocked[0].critical_cve_count == verdict.critical_cve_count


async def test_queue_never_reaches_another_team(db_session: AsyncSession) -> None:
    """Cross-team isolation, asserted on the aggregate path specifically.

    The per-project gate endpoint checks access before evaluating. This path
    has no project parameter to check — isolation comes entirely from the
    scoping helper, so it needs its own assertion.
    """
    from services.action_queue_service import get_action_queue

    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)

    _, scan_b = await _project_with_scan(db_session, team=team_b)
    await _critical_finding(db_session, scan_id=scan_b)

    user_a = await make_user(db_session)
    await make_membership(db_session, user=user_a, team=team_a, role="developer")

    queue = await get_action_queue(db_session, actor=principal_for(user_a, team_ids=[team_a.id]))

    assert queue.gate_blocked == [], "team A saw a team B project in the action queue"


async def test_stale_bucket_counts_a_project_that_never_scanned(
    db_session: AsyncSession,
) -> None:
    """Registered and forgotten is the case a "last scan" filter misses.

    A project with no succeeded scan has no last-scan timestamp to compare, so
    the naive query drops it — exactly the project most worth surfacing.
    """
    from services.action_queue_service import STALE_SCAN_DAYS, _stale_projects

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project = await make_project(db_session, team=team)

    # Pin the clock past the threshold rather than backdating the row, so the
    # test does not depend on how the factory stamps created_at.
    later = datetime.now(tz=UTC) + timedelta(days=STALE_SCAN_DAYS + 1)
    stale = await _stale_projects(db_session, project_ids=[project.id], now=later)

    assert [s.project_id for s in stale] == [project.id]
    assert stale[0].last_succeeded_at is None


async def _kev_finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    due_date,
    status: str = "new",
) -> None:
    from models import ScanComponent, Vulnerability, VulnerabilityFinding

    cv_id = await _component_version(session)
    session.add(
        ScanComponent(
            scan_id=scan_id, component_version_id=cv_id, direct=True, raw_data={}
        )
    )
    vuln = Vulnerability(
        external_id=f"CVE-2024-{unique_suffix()}",
        source="NVD",
        severity="high",
        summary="kev fixture",
        kev=True,
        kev_due_date=due_date,
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv_id,
            vulnerability_id=vuln.id,
            status=status,
        )
    )
    await session.commit()


@pytest.mark.parametrize(
    ("label", "offset_days", "status", "expect_overdue", "expect_due_soon"),
    [
        ("a week past due", -7, "new", 1, 0),
        ("due yesterday", -1, "new", 1, 0),
        # The boundary in both directions. `today` is neither overdue nor
        # excluded — it is the last day to act, which is due_soon.
        ("due today", 0, "new", 0, 1),
        ("due at the edge of the window", 7, "new", 0, 1),
        ("due just past the window", 8, "new", 0, 0),
        # Triage closes the work regardless of the deadline.
        ("overdue but dismissed", -7, "not_affected", 0, 0),
        ("due soon but fixed", 3, "fixed", 0, 0),
    ],
)
async def test_kev_sla_buckets_by_due_date_and_status(
    db_session: AsyncSession,
    label: str,
    offset_days: int,
    status: str,
    expect_overdue: int,
    expect_due_soon: int,
) -> None:
    """KEV bucketing, including both edges of the "due soon" window.

    The window is a date comparison, so the boundaries are where an
    off-by-one lives. Pinning `today` rather than using wall time means the
    test does not change meaning when it runs near midnight.
    """
    from datetime import timedelta as _timedelta

    from services.action_queue_service import _kev_sla

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project_id, scan_id = await _project_with_scan(db_session, team=team)

    today = datetime.now(tz=UTC).date()
    await _kev_finding(
        db_session,
        scan_id=scan_id,
        due_date=today + _timedelta(days=offset_days),
        status=status,
    )

    from services.dashboard_service import _latest_succeeded_scan_ids

    scan_ids = await _latest_succeeded_scan_ids(db_session, project_ids=[project_id])
    bucket = await _kev_sla(db_session, scan_ids=scan_ids, today=today)

    assert bucket.overdue == expect_overdue, label
    assert bucket.due_soon == expect_due_soon, label


async def _enable_policy(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    category_overrides: dict | None = None,
    license_exceptions: list | None = None,
    unknown_license_category: str = "conditional",
):
    from models import LicensePolicy

    policy = LicensePolicy(
        organization_id=organization_id,
        team_id=team_id,
        name="parity policy",
        enabled=True,
        category_overrides=category_overrides or {},
        license_exceptions=license_exceptions or [],
        unknown_license_category=unknown_license_category,
    )
    session.add(policy)
    await session.commit()
    return policy


async def _component_with_license(
    session: AsyncSession, *, scan_id: uuid.UUID, spdx_id: str, category: str
) -> None:
    from models import License, LicenseFinding, ScanComponent

    cv_id = await _component_version(session)
    session.add(
        ScanComponent(
            scan_id=scan_id, component_version_id=cv_id, direct=True, raw_data={}
        )
    )
    lic = License(spdx_id=spdx_id, name=spdx_id, category=category)
    session.add(lic)
    await session.commit()
    await session.refresh(lic)

    session.add(
        LicenseFinding(
            scan_id=scan_id,
            component_version_id=cv_id,
            license_id=lic.id,
            kind="concluded",
            source_path=f"path-{unique_suffix()}",
        )
    )
    await session.commit()


async def test_parity_holds_when_a_policy_overrides_a_licence_to_forbidden(
    db_session: AsyncSession,
) -> None:
    """A team policy can forbid a licence the catalogue calls allowed.

    This is the case the original parity matrix could not reach: every team it
    built had no policy, so both paths took the identical static branch and
    the test proved only that they agree where they cannot differ. With an
    override in play the gate fails while a static category read sees nothing.
    """
    from services.action_queue_service import _blocked_for_projects
    from services.policy_gate import evaluate_gate

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project_id, scan_id = await _project_with_scan(db_session, team=team)

    spdx = f"MIT-{unique_suffix()}"
    await _component_with_license(
        db_session, scan_id=scan_id, spdx_id=spdx, category="allowed"
    )
    await _enable_policy(
        db_session,
        organization_id=org.id,
        team_id=team.id,
        category_overrides={spdx: "forbidden"},
    )

    verdict = await evaluate_gate(db_session, project_id)
    blocked = await _blocked_for_projects(db_session, project_ids=[project_id])

    assert verdict.gate == "fail", "the policy override should block the build"
    assert blocked, (
        "the action queue omitted a project the build gate blocks — the panel "
        "whose job is 'which builds are blocked' would hide it"
    )
    assert blocked[0].forbidden_license_count == verdict.forbidden_license_count


async def test_parity_holds_when_a_waiver_allows_a_forbidden_licence(
    db_session: AsyncSession,
) -> None:
    """The mirror: a waiver clears the gate, so the queue must not still list it."""
    from datetime import date

    from services.action_queue_service import _blocked_for_projects
    from services.policy_gate import evaluate_gate

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project_id, scan_id = await _project_with_scan(db_session, team=team)

    spdx = f"GPL-3.0-{unique_suffix()}"
    await _component_with_license(
        db_session, scan_id=scan_id, spdx_id=spdx, category="forbidden"
    )
    await _enable_policy(
        db_session,
        organization_id=org.id,
        team_id=team.id,
        license_exceptions=[
            {
                "spdx_id": spdx,
                "reason": "reviewed by legal",
                "expires_at": str(date(2099, 12, 31)),
            }
        ],
    )

    verdict = await evaluate_gate(db_session, project_id)
    blocked = await _blocked_for_projects(db_session, project_ids=[project_id])

    assert verdict.gate == "pass", "the waiver should clear the build gate"
    assert not blocked, (
        "the action queue listed a project the gate passes — a panel that "
        "cries wolf is one operators stop reading"
    )


async def test_parity_holds_when_only_the_epss_gate_blocks(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project blocked solely by EPSS must still reach the panel.

    `evaluate_gate` fails on any non-empty reason clause, and EPSS is one of
    three. An aggregate that only knew about criticals and licences reported
    an empty queue for exactly the builds an operator's own threshold was
    failing — the configuration was working and the panel denied it.
    """
    from services.action_queue_service import _blocked_for_projects
    from services.policy_gate import evaluate_gate

    monkeypatch.setenv("GATE_EPSS_THRESHOLD", "0.5")

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project_id, scan_id = await _project_with_scan(db_session, team=team)

    from models import ScanComponent, Vulnerability, VulnerabilityFinding

    cv_id = await _component_version(db_session)
    db_session.add(
        ScanComponent(
            scan_id=scan_id, component_version_id=cv_id, direct=True, raw_data={}
        )
    )
    # High, not critical, and licensed cleanly: EPSS is the only signal.
    vuln = Vulnerability(
        external_id=f"CVE-2024-{unique_suffix()}",
        source="NVD",
        severity="high",
        summary="epss fixture",
        epss_score=0.9,
    )
    db_session.add(vuln)
    await db_session.commit()
    await db_session.refresh(vuln)
    db_session.add(
        VulnerabilityFinding(
            scan_id=scan_id, component_version_id=cv_id, vulnerability_id=vuln.id
        )
    )
    await db_session.commit()

    verdict = await evaluate_gate(db_session, project_id)
    blocked = await _blocked_for_projects(db_session, project_ids=[project_id])

    assert verdict.gate == "fail"
    assert verdict.critical_cve_count == 0
    assert verdict.forbidden_license_count == 0
    assert blocked, "the queue omitted a project blocked only by the EPSS gate"
    assert blocked[0].epss_gate_count == verdict.epss_gate_count


async def test_parity_holds_when_only_a_malicious_package_blocks(
    db_session: AsyncSession,
) -> None:
    """The panel must list a build blocked solely by a malicious package.

    This axis is the one an operator most needs to see in a list of blocked
    builds — the response is removal plus credential rotation, not a scheduled
    upgrade — and it is the one an aggregate written before the axis existed
    silently omits.
    """
    from services.action_queue_service import _blocked_for_projects
    from services.policy_gate import evaluate_gate

    org = await make_organization(db_session)
    team = await make_team(db_session, organization=org)
    project_id, scan_id = await _project_with_scan(db_session, team=team)

    from models import ComponentVersion, ScanComponent

    cv_id = await _component_version(db_session)
    cv = await db_session.get(ComponentVersion, cv_id)
    assert cv is not None
    cv.malicious_state = "flagged"
    cv.malicious_id = "MAL-0000-PARITY"
    cv.malicious_source = "osv.dev@seed"
    db_session.add(cv)
    db_session.add(
        ScanComponent(
            scan_id=scan_id, component_version_id=cv_id, direct=True, raw_data={}
        )
    )
    await db_session.commit()

    verdict = await evaluate_gate(db_session, project_id)
    blocked = await _blocked_for_projects(db_session, project_ids=[project_id])

    assert verdict.gate == "fail"
    assert verdict.critical_cve_count == 0
    assert verdict.forbidden_license_count == 0
    assert verdict.malicious_component_count == 1
    assert blocked, "the queue omitted a project blocked only by a malicious package"
    assert blocked[0].malicious_component_count == verdict.malicious_component_count
