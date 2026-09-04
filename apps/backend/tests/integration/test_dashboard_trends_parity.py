"""The portfolio trend's flows must agree with the per-project release diff.

Why this test carries the design
--------------------------------
``services/dashboard_trends_service.py`` answers the same question
``services/project_diff_service.py`` answers — what opened and what closed
between two snapshots — for every project at once. It cannot call the diff: one
call per scan pair over a 90-day window would be hundreds of round trips per
render, and the diff also computes components and licences that the chart does
not use. So the rule for "what is an open exposure" now lives in two places,
which CLAUDE.md hardening rule #2 identifies as the shape defects hide in.

This test is the price. It seeds one team per scenario, computes the trend for
a member of that team, computes the diff for the same two scans, and fails on
disagreement — plus a portfolio member of every team, so the sum is checked as
well as the parts.

The matrix is chosen for states where the two *can* differ, which is the lesson
the action-queue parity test learned the expensive way (its first version built
only teams without a licence policy, so both paths took the identical branch and
it proved agreement where disagreement was impossible):

  - one CVE against two packages that share a version string — the exact case a
    ``(cve id, version)`` key collapsed into one exposure;
  - a finding triaged to ``not_affected``, and one suppressed via VEX — the
    closed-status tuple is imported from the diff, and ``suppressed`` is
    precisely where it differs from ``policy_gate``'s;
  - a finding that was closed in the base and is open in the target, which is
    "new" without anything having been added;
  - a failed scan between the two succeeded ones, which must not become the
    predecessor;
  - a project's first ever scan, where the diff has no base to compare against
    and the oracle is the diff service's own open-exposure helper.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.security import CurrentUser
from services.dashboard_trends_service import get_dashboard_trends
from services.project_diff_service import _open_findings_by_key, diff_release_snapshots
from tests._db_required import migrate_to_head
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

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
BASE_DAYS_AGO = 4
TARGET_DAYS_AGO = 1


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


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
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """One project with a base and a target scan, plus a member who can see it."""

    name: str
    project_id: uuid.UUID
    base_scan_id: uuid.UUID
    target_scan_id: uuid.UUID
    actor: CurrentUser


async def _component_version(session: AsyncSession, *, version: str = "1.0.0") -> uuid.UUID:
    from models import Component, ComponentVersion

    suffix = unique_suffix()
    purl = f"pkg:npm/pkg-{suffix}"
    component = Component(purl=purl, package_type="npm", name=f"pkg-{suffix}")
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
    return cv.id


async def _vulnerability(session: AsyncSession, *, severity: str = "critical") -> uuid.UUID:
    from models import Vulnerability

    suffix = unique_suffix()
    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"parity fixture {suffix}",
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)
    return vuln.id


async def _finding(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    cv_id: uuid.UUID,
    vulnerability_id: uuid.UUID,
    status: str = "new",
) -> None:
    from models import VulnerabilityFinding

    session.add(
        VulnerabilityFinding(
            scan_id=scan_id,
            component_version_id=cv_id,
            vulnerability_id=vulnerability_id,
            status=status,
        )
    )
    await session.commit()


async def _team_with_member(session: AsyncSession, organization):
    team = await make_team(session, organization=organization)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    return team, user


# ---------------------------------------------------------------------------
# Scenario builders — each returns a project whose two scans differ in one way
# the two implementations could disagree about.
# ---------------------------------------------------------------------------


async def _seed_plain(session: AsyncSession, *, project, base, target) -> None:
    """One exposure kept, one removed, one added."""
    kept_cv = await _component_version(session)
    kept_vuln = await _vulnerability(session)
    gone_cv = await _component_version(session)
    gone_vuln = await _vulnerability(session)
    fresh_cv = await _component_version(session)
    fresh_vuln = await _vulnerability(session)

    await _finding(session, scan_id=base.id, cv_id=kept_cv, vulnerability_id=kept_vuln)
    await _finding(session, scan_id=base.id, cv_id=gone_cv, vulnerability_id=gone_vuln)
    await _finding(session, scan_id=target.id, cv_id=kept_cv, vulnerability_id=kept_vuln)
    await _finding(session, scan_id=target.id, cv_id=fresh_cv, vulnerability_id=fresh_vuln)


async def _seed_shared_cve_same_version(
    session: AsyncSession, *, project, base, target
) -> None:
    """One CVE against two packages that both sit at 1.0.0; one is dropped.

    Under a ``(cve id, version string)`` key the two are one entry, so the
    removal is invisible and the diff reports nothing resolved. Under the
    exposure key it is one resolved out of two.
    """
    shared_vuln = await _vulnerability(session)
    first_cv = await _component_version(session, version="1.0.0")
    second_cv = await _component_version(session, version="1.0.0")

    await _finding(session, scan_id=base.id, cv_id=first_cv, vulnerability_id=shared_vuln)
    await _finding(session, scan_id=base.id, cv_id=second_cv, vulnerability_id=shared_vuln)
    await _finding(session, scan_id=target.id, cv_id=first_cv, vulnerability_id=shared_vuln)


async def _seed_triaged(session: AsyncSession, *, project, base, target) -> None:
    """Same finding both sides; the target's is triaged to ``not_affected``."""
    cv_id = await _component_version(session)
    vuln_id = await _vulnerability(session)
    await _finding(session, scan_id=base.id, cv_id=cv_id, vulnerability_id=vuln_id)
    await _finding(
        session,
        scan_id=target.id,
        cv_id=cv_id,
        vulnerability_id=vuln_id,
        status="not_affected",
    )


async def _seed_suppressed(session: AsyncSession, *, project, base, target) -> None:
    """Suppressed via VEX — where the diff's closed set and the gate's part ways.

    ``policy_gate`` keeps a suppressed finding blocking a release; the diff, and
    therefore this chart, treat it as no longer the user's exposure. Importing
    one tuple from the other is what keeps that a deliberate difference rather
    than a drift.
    """
    cv_id = await _component_version(session)
    vuln_id = await _vulnerability(session)
    await _finding(session, scan_id=base.id, cv_id=cv_id, vulnerability_id=vuln_id)
    await _finding(
        session,
        scan_id=target.id,
        cv_id=cv_id,
        vulnerability_id=vuln_id,
        status="suppressed",
    )


async def _seed_reopened(session: AsyncSession, *, project, base, target) -> None:
    """Closed in the base, open in the target — new exposure, nothing added."""
    cv_id = await _component_version(session)
    vuln_id = await _vulnerability(session)
    await _finding(
        session, scan_id=base.id, cv_id=cv_id, vulnerability_id=vuln_id, status="fixed"
    )
    await _finding(session, scan_id=target.id, cv_id=cv_id, vulnerability_id=vuln_id)


async def _seed_unchanged(session: AsyncSession, *, project, base, target) -> None:
    """Identical snapshots — both paths must report a quiet day."""
    cv_id = await _component_version(session)
    vuln_id = await _vulnerability(session)
    await _finding(session, scan_id=base.id, cv_id=cv_id, vulnerability_id=vuln_id)
    await _finding(session, scan_id=target.id, cv_id=cv_id, vulnerability_id=vuln_id)


SCENARIOS = {
    "plain": _seed_plain,
    "shared_cve_same_version": _seed_shared_cve_same_version,
    "triaged": _seed_triaged,
    "suppressed": _seed_suppressed,
    "reopened": _seed_reopened,
    "unchanged": _seed_unchanged,
}


async def _build_scenario(
    session: AsyncSession,
    *,
    name: str,
    organization,
    with_failed_scan: bool = False,
    with_other_lineage: bool = False,
    base_days_ago: int = BASE_DAYS_AGO,
) -> Scenario:
    team, user = await _team_with_member(session, organization)
    project = await make_project(session, team=team)
    base = await make_scan(
        session,
        project=project,
        status="succeeded",
        ref="main",
        created_at=NOW - timedelta(days=base_days_ago),
    )
    if with_failed_scan:
        # Between the two, and newer than the base: if it were taken as the
        # predecessor, the target's whole open set would read as new.
        await make_scan(
            session,
            project=project,
            status="failed",
            ref="main",
            created_at=NOW - timedelta(days=2),
        )
    if with_other_lineage:
        # A container scan of the same project, sitting between the two source
        # scans. It looks at OS packages, not language packages, so treating it
        # as the target's predecessor would report the whole source set new and
        # the whole OS set resolved.
        other = await make_scan(
            session,
            project=project,
            status="succeeded",
            kind="container",
            ref="main",
            created_at=NOW - timedelta(days=2),
        )
        cv_id = await _component_version(session)
        vuln_id = await _vulnerability(session)
        await _finding(session, scan_id=other.id, cv_id=cv_id, vulnerability_id=vuln_id)
    target = await make_scan(
        session,
        project=project,
        status="succeeded",
        ref="main",
        created_at=NOW - timedelta(days=TARGET_DAYS_AGO),
    )
    await SCENARIOS[name](session, project=project, base=base, target=target)
    return Scenario(
        name=name,
        project_id=project.id,
        base_scan_id=base.id,
        target_scan_id=target.id,
        actor=principal_for(user, team_ids=[team.id]),
    )


def _point_on(trends, *, days_ago: int):
    target = (NOW - timedelta(days=days_ago)).date()
    matches = [point for point in trends.points if point.date == target]
    assert matches, f"no point for {target}"
    return matches[0]


# ---------------------------------------------------------------------------
# Parity — per scenario
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario_name", sorted(SCENARIOS))
@pytest.mark.parametrize(
    "interference",
    ["none", "failed_scan", "other_lineage", "base_outside_window"],
)
async def test_the_trend_flow_matches_the_release_diff(
    db_session: AsyncSession, scenario_name: str, interference: str
) -> None:
    """Both paths, same two scans, same numbers.

    The second axis is about how each side finds the base. The diff is handed
    both anchors explicitly; the trend picks one with a window function, and
    every way that pick can go wrong is a way the two silently diverge:

    - ``failed_scan`` — a ``LAG`` that forgot to filter on status.
    - ``other_lineage`` — a container scan of the same project between the
      two, which is not a comparable snapshot of anything the source scans
      looked at.
    - ``base_outside_window`` — the base sits ten days back while the window
      is seven, so a ``LAG`` bounded by ``start_ts`` (the obvious
      optimisation) would find no predecessor and report nothing at all.
    """
    organization = await make_organization(db_session)
    scenario = await _build_scenario(
        db_session,
        name=scenario_name,
        organization=organization,
        with_failed_scan=interference == "failed_scan",
        with_other_lineage=interference == "other_lineage",
        base_days_ago=10 if interference == "base_outside_window" else BASE_DAYS_AGO,
    )

    diff = await diff_release_snapshots(
        db_session,
        project_id=scenario.project_id,
        actor=scenario.actor,
        base_scan_id=scenario.base_scan_id,
        target_scan_id=scenario.target_scan_id,
    )
    trends = await get_dashboard_trends(
        db_session, actor=scenario.actor, days=7, now=NOW
    )

    # The oracle is a list, and the diff truncates its lists at `_MAX_LIST`.
    # Every fixture here is far below that, but asserting it makes the
    # oracle's validity explicit rather than incidental: above the cap the
    # two implementations disagree by construction, and a future fixture that
    # grew past it would look like a parity failure.
    assert diff["truncated"] is False

    point = _point_on(trends, days_ago=TARGET_DAYS_AGO)
    assert point.new_findings == len(diff["vulnerabilities"]["introduced"]), (
        f"{scenario_name}: trend says {point.new_findings} new, "
        f"diff says {len(diff['vulnerabilities']['introduced'])}"
    )
    assert point.resolved_findings == len(diff["vulnerabilities"]["resolved"]), (
        f"{scenario_name}: trend says {point.resolved_findings} resolved, "
        f"diff says {len(diff['vulnerabilities']['resolved'])}"
    )


async def test_the_shared_cve_scenario_actually_moves_a_number(
    db_session: AsyncSession,
) -> None:
    """A parity case only proves something if the number is not zero.

    This is the state the older key collapsed. If both sides ever return zero
    here, the parity assertion above passes while proving nothing, so the
    expected value is pinned in its own test rather than left implicit.
    """
    organization = await make_organization(db_session)
    scenario = await _build_scenario(
        db_session, name="shared_cve_same_version", organization=organization
    )

    diff = await diff_release_snapshots(
        db_session,
        project_id=scenario.project_id,
        actor=scenario.actor,
        base_scan_id=scenario.base_scan_id,
        target_scan_id=scenario.target_scan_id,
    )

    assert len(diff["vulnerabilities"]["resolved"]) == 1
    assert len(diff["vulnerabilities"]["introduced"]) == 0


async def test_a_scan_with_no_predecessor_reports_a_level_and_no_flow(
    db_session: AsyncSession,
) -> None:
    """The one case the diff cannot answer, checked against its own helper.

    A scan with nothing before it has no diff — there is no base — so the
    trend reports no flow for it. The exposure is not lost: it lands in the
    level, and the level must equal the scan's open set. The oracle is
    ``_open_findings_by_key``, the same function the diff builds both of its
    sides from, rather than a number written out by hand here.
    """
    organization = await make_organization(db_session)
    team, user = await _team_with_member(db_session, organization)
    project = await make_project(db_session, team=team)
    scan = await make_scan(
        db_session,
        project=project,
        status="succeeded",
        created_at=NOW - timedelta(days=TARGET_DAYS_AGO),
    )
    shared_vuln = await _vulnerability(db_session)
    for _ in range(2):
        cv_id = await _component_version(db_session, version="2.3.4")
        await _finding(
            db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=shared_vuln
        )

    open_set = await _open_findings_by_key(db_session, scan_id=scan.id)
    trends = await get_dashboard_trends(
        db_session,
        actor=principal_for(user, team_ids=[team.id]),
        days=7,
        now=NOW,
    )

    point = _point_on(trends, days_ago=TARGET_DAYS_AGO)
    assert (point.new_findings, point.resolved_findings) == (0, 0)
    assert point.critical_open == len(open_set) == 2


# ---------------------------------------------------------------------------
# Parity — the portfolio sum
# ---------------------------------------------------------------------------


async def test_the_portfolio_total_is_the_sum_of_the_project_diffs(
    db_session: AsyncSession,
) -> None:
    """Every scenario at once, seen by one member of all of them.

    Per-scenario parity can hold while the portfolio query double-counts or
    drops a project — the anti-joins group by scan, and a grouping mistake
    only shows up once more than one project is in scope.
    """
    organization = await make_organization(db_session)
    scenarios = [
        await _build_scenario(db_session, name=name, organization=organization)
        for name in sorted(SCENARIOS)
    ]

    portfolio_user = await make_user(db_session)
    portfolio_team_ids: list[uuid.UUID] = []
    from sqlalchemy import select

    from models import Project, Team

    for scenario in scenarios:
        team_id = (
            await db_session.execute(
                select(Project.team_id).where(Project.id == scenario.project_id)
            )
        ).scalar_one()
        team = (
            await db_session.execute(select(Team).where(Team.id == team_id))
        ).scalar_one()
        await make_membership(
            db_session, user=portfolio_user, team=team, role="developer"
        )
        portfolio_team_ids.append(team_id)

    expected_new = 0
    expected_resolved = 0
    for scenario in scenarios:
        diff = await diff_release_snapshots(
            db_session,
            project_id=scenario.project_id,
            actor=scenario.actor,
            base_scan_id=scenario.base_scan_id,
            target_scan_id=scenario.target_scan_id,
        )
        expected_new += len(diff["vulnerabilities"]["introduced"])
        expected_resolved += len(diff["vulnerabilities"]["resolved"])

    trends = await get_dashboard_trends(
        db_session,
        actor=principal_for(portfolio_user, team_ids=portfolio_team_ids),
        days=7,
        now=NOW,
    )

    point = _point_on(trends, days_ago=TARGET_DAYS_AGO)
    assert point.scan_count == len(scenarios)
    assert point.new_findings == expected_new
    assert point.resolved_findings == expected_resolved
    assert expected_resolved > 0, "the matrix stopped exercising resolution"


async def test_todays_level_matches_what_the_summary_endpoint_reports(
    db_session: AsyncSession,
) -> None:
    """The levels have their own oracle, and it is the summary endpoint.

    The module docstring makes a checkable promise — "the last point of the
    series agrees with what ``/summary`` reports for today" — and a promise
    with no test is how the gap between two aggregations opens. The two
    resolve "the latest succeeded scan per project" through different code
    (``_anchor_scan_stmt`` here, ``_latest_succeeded_scan_ids`` there), so
    they can disagree about which scan is current whenever a project has two
    scans on one day or a tie in ``created_at``.

    ``/summary`` counts *components* by worst severity and this counts
    *exposures*, so the numbers coincide only when each component carries one
    CVE — which is what this fixture builds, deliberately, so the comparison
    is meaningful rather than approximate.
    """
    from services.dashboard_service import get_dashboard_summary

    organization = await make_organization(db_session)
    team, user = await _team_with_member(db_session, organization)
    actor = principal_for(user, team_ids=[team.id])

    for critical_count in (2, 1):
        project = await make_project(db_session, team=team)
        earlier = await make_scan(
            db_session,
            project=project,
            status="succeeded",
            ref="main",
            created_at=NOW - timedelta(days=3),
        )
        # A second scan on the same day as the newest one: whichever of the two
        # is picked as "current" decides the answer, and both sides have to
        # pick the same one.
        latest = await make_scan(
            db_session,
            project=project,
            status="succeeded",
            ref="main",
            created_at=NOW - timedelta(hours=2),
        )
        stale_cv = await _component_version(db_session)
        stale_vuln = await _vulnerability(db_session)
        await _finding(
            db_session, scan_id=earlier.id, cv_id=stale_cv, vulnerability_id=stale_vuln
        )
        for _ in range(critical_count):
            cv_id = await _component_version(db_session)
            vuln_id = await _vulnerability(db_session, severity="critical")
            await _finding(
                db_session, scan_id=latest.id, cv_id=cv_id, vulnerability_id=vuln_id
            )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)
    summary = await get_dashboard_summary(db_session, actor=actor)

    assert trends.points[-1].critical_open == (
        summary.vulnerability_severity_counts.critical
    ), "the series' last point disagrees with /summary about today"
    assert trends.points[-1].critical_open == 3
