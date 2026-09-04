"""DB-backed service tests for ``services/dashboard_trends_service.py``.

Backs ``GET /v1/dashboard/trends``. These run against the live Postgres
(``integration`` mark + alembic fixture) because the whole design lives in the
database: a ``LAG`` window function to find each scan's predecessor, two
anti-joins for the set difference, and a uniqueness constraint the counts rely
on. A mocked session would test the mock.

The clock is pinned through ``now=`` in every case. The window is computed in
UTC days, so a test seeded relative to wall time would be a test that fails
for whoever runs it near midnight.

Cases, in the order they appear:
  - shape: an actor with no projects still gets one point per day, all zeros.
  - flows: a project's first scan is all-new; the next scan reports only what
    changed either way.
  - levels: carried forward on days nobody scanned, and seeded from the last
    scan *before* the window opened.
  - a failed scan between two succeeded ones changes nothing.
  - triage (``not_affected``) reads as resolved even though the CVE is still
    attached to the component.
  - the same CVE on two packages at one version is two exposures; the same
    CVE recorded twice against one package version is one.
  - CROSS-TEAM ISOLATION: team B's scans never enter a team-A member's series.
  - the day window is a closed set — an arbitrary one raises rather than
    quietly scanning years of history.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from services.dashboard_trends_service import get_dashboard_trends
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

# Pinned so every seeded scan sits at a known offset from "today".
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


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
# Factories
# ---------------------------------------------------------------------------


async def _make_component_version(
    session: AsyncSession, *, version: str = "1.0.0"
) -> uuid.UUID:
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


async def _make_vulnerability(
    session: AsyncSession, *, severity: str = "critical", kev: bool = False
) -> uuid.UUID:
    from models import Vulnerability

    suffix = unique_suffix()
    vuln = Vulnerability(
        external_id=f"CVE-2026-{suffix}",
        source="NVD",
        severity=severity,
        summary=f"trend fixture {suffix}",
        kev=kev,
    )
    session.add(vuln)
    await session.commit()
    await session.refresh(vuln)
    return vuln.id


async def _attach_finding(
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


async def _member_of_new_team(session: AsyncSession):
    org = await make_organization(session)
    team = await make_team(session, organization=org)
    user = await make_user(session)
    await make_membership(session, user=user, team=team, role="developer")
    return team, principal_for(user, team_ids=[team.id])


def _point_on(trends, *, days_ago: int):
    """The series point ``days_ago`` days before the pinned clock."""
    target = (NOW - timedelta(days=days_ago)).date()
    matches = [point for point in trends.points if point.date == target]
    assert matches, f"no point for {target} in {[p.date for p in trends.points]}"
    return matches[0]


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days", [7, 30, 90])
async def test_a_caller_with_no_projects_gets_a_full_zero_series(
    db_session: AsyncSession, days: int
) -> None:
    """An empty portfolio is an empty chart, not a missing one.

    The widget draws whatever it is handed, so a short series would render as
    a truncated axis rather than an empty state.
    """
    user = await make_user(db_session)

    trends = await get_dashboard_trends(
        db_session, actor=principal_for(user, team_roles={}), days=days, now=NOW
    )

    assert trends.period_days == days
    assert len(trends.points) == days
    assert trends.points[0].date == (NOW - timedelta(days=days - 1)).date()
    assert trends.points[-1].date == NOW.date()
    assert trends.project_count == 0
    assert trends.totals.new_findings == 0
    assert all(point.critical_open == 0 for point in trends.points)


async def test_dates_are_contiguous_and_ordered(db_session: AsyncSession) -> None:
    user = await make_user(db_session)

    trends = await get_dashboard_trends(
        db_session, actor=principal_for(user, team_roles={}), days=30, now=NOW
    )

    dates = [point.date for point in trends.points]
    assert dates == sorted(dates)
    assert all(
        (later - earlier).days == 1 for earlier, later in zip(dates, dates[1:], strict=False)
    )


# ---------------------------------------------------------------------------
# Flows
# ---------------------------------------------------------------------------


async def test_a_scan_with_no_predecessor_contributes_no_flow(
    db_session: AsyncSession,
) -> None:
    """"Nothing came before" is not something this schema can tell us.

    Scan retention hard-deletes superseded scans, so a project scanned daily
    for a year keeps only its last week on disk. Treating a scan without a
    predecessor as a first scan would put a spike of hundreds of "new"
    findings on the day the surviving history begins, for exposure that had
    been sitting there for months. The level still shows the project's
    standing exposure, which is where it belongs.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    scan = await make_scan(
        db_session,
        project=project,
        status="succeeded",
        created_at=NOW - timedelta(days=3),
    )
    for _ in range(2):
        cv_id = await _make_component_version(db_session)
        vuln_id = await _make_vulnerability(db_session)
        await _attach_finding(
            db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=vuln_id
        )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    point = _point_on(trends, days_ago=3)
    assert point.new_findings == 0
    assert point.resolved_findings == 0
    assert point.scan_count == 1
    assert point.critical_open == 2, "the exposure has to surface in the level"
    assert trends.totals.new_findings == 0


async def test_a_predecessor_older_than_the_window_still_counts(
    db_session: AsyncSession,
) -> None:
    """The LAG deliberately has no lower time bound, and this is why.

    A weekly-scanned project's first scan inside a 7-day window is only new
    relative to the scan before it, which sits outside the window. Bounding
    the window function by ``start_ts`` — the obvious optimisation — would
    make that scan look like a first scan and, before the no-predecessor rule,
    report its whole open set as new. Now it would silently report nothing at
    all, which is why this case is asserted rather than left to the docstring.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    kept_cv = await _make_component_version(db_session)
    kept_vuln = await _make_vulnerability(db_session)
    fresh_cv = await _make_component_version(db_session)
    fresh_vuln = await _make_vulnerability(db_session)

    old = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=20)
    )
    await _attach_finding(
        db_session, scan_id=old.id, cv_id=kept_cv, vulnerability_id=kept_vuln
    )
    recent = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=2)
    )
    await _attach_finding(
        db_session, scan_id=recent.id, cv_id=kept_cv, vulnerability_id=kept_vuln
    )
    await _attach_finding(
        db_session, scan_id=recent.id, cv_id=fresh_cv, vulnerability_id=fresh_vuln
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    point = _point_on(trends, days_ago=2)
    assert point.new_findings == 1, "the pre-window scan was not used as the base"
    assert point.resolved_findings == 0
    assert trends.points[0].critical_open == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("kind", "container"), ("ref", "pr-12")],
)
async def test_a_different_lineage_does_not_manufacture_churn(
    db_session: AsyncSession, field: str, value: str
) -> None:
    """Two scans are comparable only if they looked at the same thing.

    A container scan enumerates OS packages where a source scan enumerates
    language packages, and a pull-request scan looks at a different branch.
    Diffing across either boundary reports the whole first set resolved and
    the whole second set new, then the inverse on the next scan — a chart that
    oscillates between two unrelated numbers while nothing changed. The
    retention model already draws this line by superseding only same-ref
    scans; the series follows it.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    main_cv = await _make_component_version(db_session)
    main_vuln = await _make_vulnerability(db_session)
    other_cv = await _make_component_version(db_session)
    other_vuln = await _make_vulnerability(db_session)

    first = await make_scan(
        db_session,
        project=project,
        status="succeeded",
        ref="main",
        created_at=NOW - timedelta(days=5),
    )
    await _attach_finding(
        db_session, scan_id=first.id, cv_id=main_cv, vulnerability_id=main_vuln
    )

    interloper_kwargs: dict[str, str] = {"kind": "source", "ref": "main"}
    interloper_kwargs[field] = value
    interloper = await make_scan(
        db_session,
        project=project,
        status="succeeded",
        created_at=NOW - timedelta(days=3),
        **interloper_kwargs,
    )
    await _attach_finding(
        db_session, scan_id=interloper.id, cv_id=other_cv, vulnerability_id=other_vuln
    )

    second = await make_scan(
        db_session,
        project=project,
        status="succeeded",
        ref="main",
        created_at=NOW - timedelta(days=1),
    )
    await _attach_finding(
        db_session, scan_id=second.id, cv_id=main_cv, vulnerability_id=main_vuln
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    # The main lineage did not change, so nothing moved on its day.
    latest = _point_on(trends, days_ago=1)
    assert (latest.new_findings, latest.resolved_findings) == (0, 0)
    # And the other lineage's first scan has no predecessor of its own, so it
    # reports nothing either — rather than "everything resolved, everything new".
    interloper_point = _point_on(trends, days_ago=3)
    assert (interloper_point.new_findings, interloper_point.resolved_findings) == (0, 0)


async def test_the_next_scan_reports_only_what_changed(
    db_session: AsyncSession,
) -> None:
    """The set difference, not the difference of the totals.

    Both scans carry two exposures, so a chart built on totals would report a
    quiet day. One was fixed and one appeared.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)

    kept_cv = await _make_component_version(db_session)
    kept_vuln = await _make_vulnerability(db_session)
    gone_cv = await _make_component_version(db_session)
    gone_vuln = await _make_vulnerability(db_session)
    fresh_cv = await _make_component_version(db_session)
    fresh_vuln = await _make_vulnerability(db_session)

    first = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=4)
    )
    await _attach_finding(
        db_session, scan_id=first.id, cv_id=kept_cv, vulnerability_id=kept_vuln
    )
    await _attach_finding(
        db_session, scan_id=first.id, cv_id=gone_cv, vulnerability_id=gone_vuln
    )

    second = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=2)
    )
    await _attach_finding(
        db_session, scan_id=second.id, cv_id=kept_cv, vulnerability_id=kept_vuln
    )
    await _attach_finding(
        db_session, scan_id=second.id, cv_id=fresh_cv, vulnerability_id=fresh_vuln
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    later = _point_on(trends, days_ago=2)
    assert (later.new_findings, later.resolved_findings) == (1, 1)
    # The first scan has no predecessor and so contributes no flow; only the
    # second scan's change reaches the totals.
    assert trends.totals.new_findings == 1
    assert trends.totals.resolved_findings == 1


async def test_triaged_away_counts_as_resolved(db_session: AsyncSession) -> None:
    """``not_affected`` is the answer to "am I exposed", which is the question.

    The finding row is still attached to the component in the newer scan, so a
    diff that keyed on rows rather than open state would report nothing.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    cv_id = await _make_component_version(db_session)
    vuln_id = await _make_vulnerability(db_session)

    first = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=5)
    )
    await _attach_finding(
        db_session, scan_id=first.id, cv_id=cv_id, vulnerability_id=vuln_id
    )
    second = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=1)
    )
    await _attach_finding(
        db_session,
        scan_id=second.id,
        cv_id=cv_id,
        vulnerability_id=vuln_id,
        status="not_affected",
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    point = _point_on(trends, days_ago=1)
    assert (point.new_findings, point.resolved_findings) == (0, 1)
    assert point.critical_open == 0


async def test_one_cve_on_two_packages_is_two_exposures(
    db_session: AsyncSession,
) -> None:
    """The case a ``(cve id, version string)`` key silently merged.

    Two packages published in lockstep, both at 1.0.0, both hit by the same
    CVE. That is two things to fix, and the level has to say two.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    shared_vuln = await _make_vulnerability(db_session)
    first_cv = await _make_component_version(db_session, version="1.0.0")
    second_cv = await _make_component_version(db_session, version="1.0.0")

    scan = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=2)
    )
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=first_cv, vulnerability_id=shared_vuln
    )
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=second_cv, vulnerability_id=shared_vuln
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    point = _point_on(trends, days_ago=2)
    assert point.critical_open == 2


async def test_the_schema_forbids_a_duplicate_exposure_row(
    db_session: AsyncSession,
) -> None:
    """The counts rely on the triple being unique per scan — so prove it is.

    The service counts finding rows without de-duplicating them, which is only
    correct while ``uq_vuln_findings_scan_version_vuln`` holds. If that
    constraint were ever dropped, every count in this module would start
    inflating and nothing else would say so.
    """
    from sqlalchemy.exc import IntegrityError

    team, _ = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    cv_id = await _make_component_version(db_session)
    vuln_id = await _make_vulnerability(db_session)
    scan = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=2)
    )
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=vuln_id
    )

    with pytest.raises(IntegrityError, match="uq_vuln_findings_scan_version_vuln"):
        await _attach_finding(
            db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=vuln_id
        )
    await db_session.rollback()


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------


async def test_levels_carry_forward_on_days_nobody_scanned(
    db_session: AsyncSession,
) -> None:
    """A quiet week is not a clean week.

    The exposure a scan measured on Monday is still there on Tuesday; a series
    that dropped to zero would read as the risk having been fixed.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    cv_id = await _make_component_version(db_session)
    critical = await _make_vulnerability(db_session, severity="critical", kev=True)

    scan = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=4)
    )
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=critical
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    assert _point_on(trends, days_ago=5).critical_open == 0
    for days_ago in (4, 3, 2, 1, 0):
        point = _point_on(trends, days_ago=days_ago)
        assert point.critical_open == 1, f"level lost {days_ago} days ago"
        assert point.kev_open == 1
    assert _point_on(trends, days_ago=0).scan_count == 0


async def test_the_window_opens_at_the_level_the_last_scan_left(
    db_session: AsyncSession,
) -> None:
    """Day one is anchored to the newest scan from *before* the window.

    Without the anchor a project scanned a month ago reads as clean all week,
    which is the opposite of what it is.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    cv_id = await _make_component_version(db_session)
    vuln_id = await _make_vulnerability(db_session)

    scan = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=40)
    )
    await _attach_finding(
        db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=vuln_id
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    assert trends.points[0].critical_open == 1
    assert trends.points[0].scan_count == 0
    # Nothing happened inside the window, so no flow is attributed to it — the
    # exposure entered the portfolio before this chart starts.
    assert trends.totals.new_findings == 0


async def test_a_failed_scan_neither_resets_the_level_nor_becomes_the_predecessor(
    db_session: AsyncSession,
) -> None:
    """Failure means "we did not measure", not "we measured nothing".

    A failed scan carries no findings, so treating it as a snapshot would
    report every open exposure as resolved and then as new again.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    cv_id = await _make_component_version(db_session)
    vuln_id = await _make_vulnerability(db_session)

    first = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=5)
    )
    await _attach_finding(
        db_session, scan_id=first.id, cv_id=cv_id, vulnerability_id=vuln_id
    )
    await make_scan(
        db_session, project=project, status="failed", created_at=NOW - timedelta(days=3)
    )
    second = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=1)
    )
    await _attach_finding(
        db_session, scan_id=second.id, cv_id=cv_id, vulnerability_id=vuln_id
    )

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    assert _point_on(trends, days_ago=3).scan_count == 0
    assert _point_on(trends, days_ago=3).critical_open == 1
    latest = _point_on(trends, days_ago=1)
    assert (latest.new_findings, latest.resolved_findings) == (0, 0)
    assert latest.critical_open == 1


async def test_a_high_severity_finding_is_not_counted_as_critical(
    db_session: AsyncSession,
) -> None:
    """The level is critical-only; the flows are not.

    Asserted together because a query that filtered severity in the wrong
    place would still look right on a portfolio of only critical fixtures.
    """
    team, actor = await _member_of_new_team(db_session)
    project = await make_project(db_session, team=team)
    cv_id = await _make_component_version(db_session)
    high = await _make_vulnerability(db_session, severity="high")

    await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=4)
    )
    scan = await make_scan(
        db_session, project=project, status="succeeded", created_at=NOW - timedelta(days=2)
    )
    await _attach_finding(db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=high)

    trends = await get_dashboard_trends(db_session, actor=actor, days=7, now=NOW)

    point = _point_on(trends, days_ago=2)
    # The flow counts every severity — a high-severity CVE appearing is still
    # something appearing.
    assert point.new_findings == 1
    # The level counts only criticals.
    assert point.critical_open == 0
    assert point.kev_open == 0


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


async def test_another_teams_project_never_enters_the_series(
    db_session: AsyncSession,
) -> None:
    """The whole security story for a route with no path parameter.

    Asserted with its mirror — a scoping bug that returns nothing at all would
    pass the isolation half on its own.
    """
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    member_a = await make_user(db_session)
    await make_membership(db_session, user=member_a, team=team_a, role="developer")

    for team, count in ((team_a, 1), (team_b, 3)):
        project = await make_project(db_session, team=team)
        scan = await make_scan(
            db_session,
            project=project,
            status="succeeded",
            created_at=NOW - timedelta(days=2),
        )
        for _ in range(count):
            cv_id = await _make_component_version(db_session)
            vuln_id = await _make_vulnerability(db_session)
            await _attach_finding(
                db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=vuln_id
            )

    trends = await get_dashboard_trends(
        db_session,
        actor=principal_for(member_a, team_ids=[team_a.id]),
        days=7,
        now=NOW,
    )

    point = _point_on(trends, days_ago=2)
    assert point.critical_open == 1, "a team-A member saw team-B exposures"
    assert point.scan_count == 1
    assert trends.project_count == 1


async def test_membership_in_both_teams_sees_both(db_session: AsyncSession) -> None:
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    member = await make_user(db_session)
    await make_membership(db_session, user=member, team=team_a, role="developer")
    await make_membership(db_session, user=member, team=team_b, role="developer")

    for team in (team_a, team_b):
        project = await make_project(db_session, team=team)
        scan = await make_scan(
            db_session,
            project=project,
            status="succeeded",
            created_at=NOW - timedelta(days=2),
        )
        cv_id = await _make_component_version(db_session)
        vuln_id = await _make_vulnerability(db_session)
        await _attach_finding(
            db_session, scan_id=scan.id, cv_id=cv_id, vulnerability_id=vuln_id
        )

    trends = await get_dashboard_trends(
        db_session,
        actor=principal_for(member, team_ids=[team_a.id, team_b.id]),
        days=7,
        now=NOW,
    )

    point = _point_on(trends, days_ago=2)
    assert point.critical_open == 2
    assert point.scan_count == 2
    assert trends.project_count == 2


async def test_the_highest_role_is_not_a_membership_signal(
    db_session: AsyncSession,
) -> None:
    """``actor.role`` says how much power, not where — CWE-863.

    A team admin of team A is still nobody in team B, so a team-admin role
    must not widen the scope by itself.
    """
    org = await make_organization(db_session)
    team_a = await make_team(db_session, organization=org)
    team_b = await make_team(db_session, organization=org)
    admin_a = await make_user(db_session)
    await make_membership(db_session, user=admin_a, team=team_a, role="team_admin")

    project_b = await make_project(db_session, team=team_b)
    scan_b = await make_scan(
        db_session, project=project_b, status="succeeded", created_at=NOW - timedelta(days=2)
    )
    cv_id = await _make_component_version(db_session)
    vuln_id = await _make_vulnerability(db_session)
    await _attach_finding(
        db_session, scan_id=scan_b.id, cv_id=cv_id, vulnerability_id=vuln_id
    )

    trends = await get_dashboard_trends(
        db_session,
        actor=principal_for(admin_a, team_ids=[team_a.id], role="team_admin"),
        days=7,
        now=NOW,
    )

    assert all(point.new_findings == 0 for point in trends.points)
    assert all(point.critical_open == 0 for point in trends.points)
    assert all(point.scan_count == 0 for point in trends.points)


# ---------------------------------------------------------------------------
# Window validation
# ---------------------------------------------------------------------------


async def test_an_arbitrary_window_is_refused(db_session: AsyncSession) -> None:
    """The route types ``days`` as a closed set; the service does not trust it.

    A widened query parameter would otherwise turn into an unbounded walk of
    scan history without anyone noticing.
    """
    user = await make_user(db_session)

    with pytest.raises(ValueError, match="days must be one of"):
        await get_dashboard_trends(
            db_session, actor=principal_for(user, team_roles={}), days=365, now=NOW
        )
