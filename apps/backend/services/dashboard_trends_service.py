# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``/v1/dashboard/trends`` — how the portfolio's exposure moved over time.

The series carries two kinds of number, and they are computed differently.

Flows (``new_findings`` / ``resolved_findings``)
-----------------------------------------------
A succeeded scan is an immutable snapshot, so "what is new" is the set
difference between a scan's open exposures and those of the same project's
previous succeeded scan — exactly what
:func:`services.project_diff_service.diff_release_snapshots` computes for one
pair of scans. This module answers the same question for every project at once.

The naive portfolio version — subtract yesterday's total from today's — is
wrong in a way that looks right: a CVE fixed in one project and a new one
found in another cancel to zero, and the chart reports a quiet day on a day
when two things happened. So the set difference is real, and it runs in the
database: the previous scan per project comes from a ``LAG`` window function,
and each side is an anti-join against the other. Only one row per scan crosses
the wire, not one row per finding.

That leaves the "what counts as an open exposure" rule living in two modules,
the duplicated-vocabulary trap of CLAUDE.md hardening rule #2. Two things pay
for it: the closed-status tuple is *imported* from ``project_diff_service``
rather than restated, and ``tests/integration/test_dashboard_trends_parity.py``
runs both paths over the same rows and fails on disagreement.

Levels (``critical_open`` / ``kev_open``)
-----------------------------------------
These are not sums of flows — they are the standing exposure on each day,
taken from each project's latest succeeded scan on or before that day. A day
nobody scanned repeats the previous day's level rather than dropping to zero,
which is why every point also carries ``scan_count``: the UI can then say
whether a level was measured or inherited.

Anchoring uses ``Scan.created_at``, the same clock
``dashboard_service._latest_succeeded_scan_ids`` orders by, so the last point
of the series agrees with what ``/summary`` reports for today. ``completed_at``
would be the more literal "when did we learn this", but it is nullable on old
rows and would let the two endpoints disagree about which scan is current.

Open, here, means what the diff means by it — a finding triaged to
``not_affected`` or suppressed via VEX is not exposure the user is still
carrying. That is deliberately broader than
``policy_gate._CLOSED_FINDING_STATUSES``, which keeps suppressed findings
blocking a release, and it is why the KEV level here can sit below the action
queue's KEV SLA bucket: one asks what the team is exposed to, the other what
policy will refuse to ship.

Cost
----
Five queries regardless of portfolio or window size. The row counts that cross
the wire are bounded by the number of succeeded scans in the window, not by
findings — scan ids are threaded between the queries as subqueries rather than
read back as bind parameters, which on a large portfolio at ninety days would
run into asyncpg's parameter ceiling. The underlying exposure sets are still
read, so the route is rate limited per actor like the action queue.

Consistency across those five reads rests on an ordering in another module: a
scan's findings are committed by the pipeline before ``mark_succeeded`` flips
its status, so a scan this module can see as succeeded already has its
findings. Without that, a scan finalising between the window read and the
level read would appear in the series with a level of zero and flatten its
project for the rest of the chart.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import structlog
from sqlalchemy import CTE, CompoundSelect, Select, String, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import Scan, Vulnerability, VulnerabilityFinding
from schemas.dashboard_trends import (
    DashboardTrends,
    TrendPoint,
    TrendTotals,
    TrendWindow,
)
from services.dashboard_service import _accessible_project_ids
from services.project_diff_service import _CLOSED_FINDING_STATUSES

logger = structlog.get_logger("dashboard_trends.service")

# Derived from the enum the route types its parameter with, so the closed set
# cannot be widened at one end only. A free-form day count would let a single
# request span years of scan history, and the chart has no more room at 365
# points than it has at 90.
ALLOWED_PERIOD_DAYS: tuple[int, ...] = tuple(int(window) for window in TrendWindow)

DEFAULT_PERIOD_DAYS = int(TrendWindow.MONTH)


def _day_bounds(*, end: date, days: int) -> tuple[datetime, datetime, date]:
    """``(start_ts, end_ts_exclusive, start_date)`` in UTC for an inclusive window.

    ``days`` counts points, not gaps: a 7-day window ending today starts six
    days ago and yields seven points.
    """
    start_date = end - timedelta(days=days - 1)
    start_ts = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_ts = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    return start_ts, end_ts, start_date


def _ordered_scans_cte(
    *,
    project_ids: list[uuid.UUID],
    end_ts: datetime,
) -> CTE:
    """Every succeeded scan up to ``end_ts``, each carrying its predecessor's id.

    "Predecessor" means the previous scan of the same **lineage** —
    ``(project, kind, ref)`` — not merely the previous scan of the project.
    Two scans are comparable snapshots only if they looked at the same thing:
    a source scan enumerates language packages and a container scan enumerates
    OS packages, so diffing one against the other reports the entire first set
    resolved and the entire second set new, then reports the inverse tomorrow.
    A pull-request scan against a branch is the same problem in miniature.
    The retention model already draws this line — ``supersede_prior_ref_scans``
    supersedes only same-ref scans — so the chart follows it rather than
    inventing a second answer.

    The ``LAG`` has to see history from before the window: the first scan
    inside a 7-day window is only "new relative to" the scan before it, which
    may be months old. Filtering to the window first would make every window's
    first scan look like a lineage's first scan.
    """
    return (
        select(
            Scan.id.label("scan_id"),
            Scan.project_id.label("project_id"),
            Scan.created_at.label("created_at"),
            func.lag(Scan.id)
            .over(
                partition_by=(Scan.project_id, Scan.kind, Scan.ref),
                order_by=(Scan.created_at, Scan.id),
            )
            .label("prev_scan_id"),
        )
        .where(Scan.project_id.in_(project_ids))
        .where(cast(Scan.status, String) == "succeeded")
        .where(Scan.created_at < end_ts)
        .cte("ordered_scans")
    )


def _open_exposures_cte(
    scan_id_source: Select[Any] | CompoundSelect | list[uuid.UUID],
) -> CTE:
    """Open ``(scan, vulnerability, component version)`` triples.

    The triple is the unit of exposure: the same CVE on two packages is two
    things to fix, while the same CVE against one package version is one
    however many rows recorded it. No de-duplication is needed for the second
    case — ``uq_vuln_findings_scan_version_vuln`` makes the triple unique per
    scan, and ``test_the_schema_forbids_a_duplicate_exposure_row`` fails if
    that constraint is ever dropped, because these counts would start
    inflating the day it is.

    The severity and KEV flags ride along rather than being joined again for
    the level counts; both are functionally determined by the vulnerability.
    """
    return (
        select(
            VulnerabilityFinding.scan_id.label("scan_id"),
            VulnerabilityFinding.vulnerability_id.label("vulnerability_id"),
            VulnerabilityFinding.component_version_id.label("component_version_id"),
            (cast(Vulnerability.severity, String) == "critical").label("is_critical"),
            Vulnerability.kev.label("is_kev"),
        )
        .select_from(VulnerabilityFinding)
        .join(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .where(VulnerabilityFinding.scan_id.in_(scan_id_source))
        .where(cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES))
        .cte("open_exposures")
    )


async def _flow_counts(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
    start_ts: datetime,
    end_ts: datetime,
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """``(new_by_scan, resolved_by_scan)`` for the scans inside the window.

    Both halves are anti-joins over the same exposure set.

    A scan with no predecessor contributes **nothing** to either flow. The
    tempting alternative — treat it as a first scan and count its whole open
    set as new — assumes "no predecessor" means "nothing came before", and in
    this schema it does not. ``scan_retention`` hard-deletes superseded scans
    past a grace period of days, so a project scanned by CI for a year has
    only its last week of history on disk; every 90-day window would open with
    a fabricated spike of hundreds of "new" findings on the day the surviving
    history begins, for exposure that had been sitting there for months. The
    same shape appears whenever a lineage starts: a new branch, a first
    container scan.

    Nothing is lost by staying quiet. A newly onboarded project's exposure
    still enters the chart — through the level series, which is where standing
    exposure belongs. The flow series answers "what changed between two
    observations", and where there is only one observation there is no answer
    to give.
    """
    ordered = _ordered_scans_cte(project_ids=project_ids, end_ts=end_ts)
    window = (
        select(ordered.c.scan_id, ordered.c.prev_scan_id)
        .where(ordered.c.created_at >= start_ts)
        .where(ordered.c.prev_scan_id.is_not(None))
        .cte("window_scans")
    )
    # The exposure set has to cover both sides of every comparison: the window
    # scans themselves and whatever each one is being compared against. It
    # stays a subquery rather than a list of ids read back into Python — one
    # bind parameter per scan would hit asyncpg's 32767-parameter ceiling on a
    # large portfolio at 90 days, and the failure mode is a 500 that only
    # appears once a deployment is big enough.
    relevant_scan_ids = select(window.c.scan_id).union(select(window.c.prev_scan_id))
    exposures = _open_exposures_cte(relevant_scan_ids)
    cur = exposures.alias("cur")
    prev = exposures.alias("prev")

    same_exposure = and_(
        prev.c.vulnerability_id == cur.c.vulnerability_id,
        prev.c.component_version_id == cur.c.component_version_id,
    )

    new_stmt = (
        select(window.c.scan_id, func.count())
        .select_from(
            window.join(cur, cur.c.scan_id == window.c.scan_id).outerjoin(
                prev,
                and_(prev.c.scan_id == window.c.prev_scan_id, same_exposure),
            )
        )
        .where(prev.c.scan_id.is_(None))
        .group_by(window.c.scan_id)
    )
    resolved_stmt = (
        select(window.c.scan_id, func.count())
        .select_from(
            window.join(prev, prev.c.scan_id == window.c.prev_scan_id).outerjoin(
                cur,
                and_(cur.c.scan_id == window.c.scan_id, same_exposure),
            )
        )
        .where(cur.c.scan_id.is_(None))
        .group_by(window.c.scan_id)
    )

    new_by_scan = {row[0]: int(row[1]) for row in (await session.execute(new_stmt)).all()}
    resolved_by_scan = {
        row[0]: int(row[1]) for row in (await session.execute(resolved_stmt)).all()
    }
    return new_by_scan, resolved_by_scan


def _level_scan_ids(
    *,
    project_ids: list[uuid.UUID],
    start_ts: datetime,
    end_ts: datetime,
) -> CompoundSelect:
    """The scans a level can be read from: in-window scans plus the anchors.

    Returned as a subquery rather than a list of ids fetched into Python. The
    ids would come back as bind parameters — one per scan — and a portfolio of
    a few hundred projects scanned daily crosses asyncpg's 32767-parameter
    ceiling somewhere inside the 90-day window, turning the whole dashboard
    into a 500 for the largest deployments and only those.
    """
    window = (
        select(Scan.id)
        .where(Scan.project_id.in_(project_ids))
        .where(cast(Scan.status, String) == "succeeded")
        .where(Scan.created_at >= start_ts)
        .where(Scan.created_at < end_ts)
    )
    # Wrapped in a derived table: DISTINCT ON needs its own ORDER BY, which
    # cannot sit directly inside a set operation.
    anchors = _anchor_scan_stmt(project_ids=project_ids, start_ts=start_ts).subquery()
    return window.union(select(anchors.c.id))


async def _level_counts(
    session: AsyncSession,
    *,
    scan_id_source: CompoundSelect,
) -> dict[uuid.UUID, tuple[int, int]]:
    """``{scan_id: (critical_open, kev_open)}`` over the given scans.

    Counted over exposures for the same reason the flows are: the unit is the
    CVE against the package version, and the schema's uniqueness constraint
    makes that one row.
    """
    exposures = _open_exposures_cte(scan_id_source)
    stmt = select(
        exposures.c.scan_id,
        func.count().filter(exposures.c.is_critical),
        func.count().filter(exposures.c.is_kev),
    ).group_by(exposures.c.scan_id)
    return {
        row[0]: (int(row[1]), int(row[2])) for row in (await session.execute(stmt)).all()
    }


async def _window_scan_rows(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
    start_ts: datetime,
    end_ts: datetime,
) -> list[tuple[uuid.UUID, uuid.UUID, datetime]]:
    """``(scan_id, project_id, created_at)`` for succeeded scans in the window."""
    stmt = (
        select(Scan.id, Scan.project_id, Scan.created_at)
        .where(Scan.project_id.in_(project_ids))
        .where(cast(Scan.status, String) == "succeeded")
        .where(Scan.created_at >= start_ts)
        .where(Scan.created_at < end_ts)
        .order_by(Scan.created_at, Scan.id)
    )
    return [(row[0], row[1], row[2]) for row in (await session.execute(stmt)).all()]


def _anchor_scan_stmt(
    *,
    project_ids: list[uuid.UUID],
    start_ts: datetime,
) -> Select[Any]:
    """The latest succeeded scan per project from *before* the window.

    Without it, day one of the series would report zero standing exposure for
    every project that happened not to be scanned that week — a portfolio that
    looks clean because nobody looked at it.

    Ordered exactly as ``dashboard_service._latest_succeeded_scan_ids`` orders,
    tie-break included, so the two endpoints never disagree about which scan a
    project's current numbers come from.
    """
    return (
        select(Scan.id, Scan.project_id)
        .distinct(Scan.project_id)
        .where(Scan.project_id.in_(project_ids))
        .where(cast(Scan.status, String) == "succeeded")
        .where(Scan.created_at < start_ts)
        .order_by(Scan.project_id, Scan.created_at.desc(), Scan.id.desc())
    )


async def _anchor_scan_rows(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
    start_ts: datetime,
) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """``(scan_id, project_id)`` for each project's pre-window anchor."""
    stmt = _anchor_scan_stmt(project_ids=project_ids, start_ts=start_ts)
    return [(row[0], row[1]) for row in (await session.execute(stmt)).all()]


def _empty_series(*, days: int, end: date) -> DashboardTrends:
    """An all-zero series — the shape a caller with no projects gets."""
    start_date = end - timedelta(days=days - 1)
    return DashboardTrends(
        period_days=days,
        start_date=start_date,
        end_date=end,
        points=[
            TrendPoint(
                date=start_date + timedelta(days=offset),
                new_findings=0,
                resolved_findings=0,
                critical_open=0,
                kev_open=0,
                scan_count=0,
            )
            for offset in range(days)
        ],
        totals=TrendTotals(new_findings=0, resolved_findings=0),
        project_count=0,
    )


async def get_dashboard_trends(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    days: int = DEFAULT_PERIOD_DAYS,
    now: datetime | None = None,
) -> DashboardTrends:
    """Build the daily portfolio series for ``actor``.

    ``now`` is injectable so tests can pin the clock instead of seeding data
    relative to wall time; a window boundary that only misbehaves near
    midnight UTC is not something to find in production.
    """
    if days not in ALLOWED_PERIOD_DAYS:
        raise ValueError(f"days must be one of {ALLOWED_PERIOD_DAYS}, got {days}")
    days = int(days)

    moment = now or datetime.now(tz=UTC)
    end_date = moment.astimezone(UTC).date()
    start_ts, end_ts, start_date = _day_bounds(end=end_date, days=days)

    project_ids = await _accessible_project_ids(session, actor=actor)
    if not project_ids:
        logger.info("dashboard_trends.empty", actor_id=str(actor.id), days=days)
        return _empty_series(days=days, end=end_date)

    window_rows = await _window_scan_rows(
        session, project_ids=project_ids, start_ts=start_ts, end_ts=end_ts
    )
    anchor_rows = await _anchor_scan_rows(
        session, project_ids=project_ids, start_ts=start_ts
    )

    new_by_scan, resolved_by_scan = await _flow_counts(
        session, project_ids=project_ids, start_ts=start_ts, end_ts=end_ts
    )
    levels_by_scan = await _level_counts(
        session,
        scan_id_source=_level_scan_ids(
            project_ids=project_ids, start_ts=start_ts, end_ts=end_ts
        ),
    )

    # Levels start at whatever each project was carrying when the window
    # opened, then move only when a scan lands.
    current: dict[uuid.UUID, tuple[int, int]] = {
        project_id: levels_by_scan.get(scan_id, (0, 0))
        for scan_id, project_id in anchor_rows
    }
    running_critical = sum(level[0] for level in current.values())
    running_kev = sum(level[1] for level in current.values())

    scans_by_day: dict[date, list[tuple[uuid.UUID, uuid.UUID]]] = {}
    for scan_id, project_id, created_at in window_rows:
        day = created_at.astimezone(UTC).date()
        scans_by_day.setdefault(day, []).append((scan_id, project_id))

    points: list[TrendPoint] = []
    total_new = 0
    total_resolved = 0
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        day_scans = scans_by_day.get(day, [])
        day_new = 0
        day_resolved = 0
        for scan_id, project_id in day_scans:
            day_new += new_by_scan.get(scan_id, 0)
            day_resolved += resolved_by_scan.get(scan_id, 0)
            level = levels_by_scan.get(scan_id, (0, 0))
            previous = current.get(project_id, (0, 0))
            running_critical += level[0] - previous[0]
            running_kev += level[1] - previous[1]
            current[project_id] = level

        total_new += day_new
        total_resolved += day_resolved
        points.append(
            TrendPoint(
                date=day,
                new_findings=day_new,
                resolved_findings=day_resolved,
                critical_open=running_critical,
                kev_open=running_kev,
                scan_count=len(day_scans),
            )
        )

    logger.info(
        "dashboard_trends.built",
        actor_id=str(actor.id),
        days=days,
        project_count=len(project_ids),
        window_scans=len(window_rows),
        new_findings=total_new,
        resolved_findings=total_resolved,
    )

    return DashboardTrends(
        period_days=days,
        start_date=start_date,
        end_date=end_date,
        points=points,
        totals=TrendTotals(new_findings=total_new, resolved_findings=total_resolved),
        project_count=len(project_ids),
    )


__all__ = [
    "ALLOWED_PERIOD_DAYS",
    "DEFAULT_PERIOD_DAYS",
    "get_dashboard_trends",
]
