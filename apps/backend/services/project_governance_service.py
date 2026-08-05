# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``/v1/projects/{id}/governance`` — the band above the project tabs.

Nothing here is a new measurement. The risk score is the Overview tab's, the
verdict is CI's, the KEV dates are the ones the action queue counts against,
the approval count is the approvals page's. This module composes them, and
composing rather than recomputing is the whole design: a band that disagreed
with the tab three pixels below it would be worse than no band.

So every number arrives through the function that already owns it:

* ``services.risk_score`` for the score, over the same distributions
  ``project_detail_service`` feeds it;
* ``services.policy_gate.evaluate_gate`` for the verdict — called directly,
  not reimplemented. The dashboard's action queue aggregates the gate's
  *inputs* instead, because it needs a verdict for every project at once and
  five to seven queries each does not scale. Here there is one project, so
  the real evaluator costs nothing and there is nothing to keep in sync;
* ``action_queue_service._kev_sla`` for the deadline buckets, and its
  ``_pending_approvals`` for the queue count, both scoped to this project.

Authorization mirrors ``project_detail_service.get_project_overview``: load
the project, then ``assert_team_access`` — ``ProjectNotFound`` (404) for a
missing project, ``ProjectForbidden`` (403) for a non-member, super-admin
bypasses. No aggregation runs before that check. (A 403 confirms the project
exists, so this is not existence-hiding and is not described as such; the
sibling routes behave the same way, and the hiding contract belongs to the
``?scan=`` anchor, which this route does not take.)

Cost
----
Roughly a dozen queries, most of them inside ``evaluate_gate``. The
independent reads are gathered rather than awaited in series, but the band is
not free: on a team running a licence policy the gate re-classifies the whole
snapshot's licences in Python, and that now sits on the project page's
critical path.

Two numbers here are drawn from different populations on purpose, and saying
so is cheaper than a reader discovering it:

* the gate's ``forbidden_license_count`` honours the team's licence policy,
  while the risk score's licence axis uses the category stored at scan time.
  A team whose policy promotes a licence to forbidden sees "Blocked" beside a
  score that does not know about the promotion. The Overview tab has the same
  split; the band merely shows both at once.
* the trend counts every component whose worst finding is critical, including
  ones triaged away — the Overview donut's population, not the gate's. A team
  that dismisses five criticals flips the gate to pass while the sparkline
  holds its shape.

"No succeeded scan" is a state, not a zero
------------------------------------------
A project nobody has scanned produces the same numbers as a clean one: score
0, no blocking findings, no overdue KEV. ``scanned`` is what separates them,
and the band has to render the two differently — a gate that never ran is not
a gate that passed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import String, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from models import Scan
from schemas.project_governance import (
    GovernanceGate,
    GovernanceKevSla,
    GovernanceTrendPoint,
    ProjectGovernance,
)
from services import risk_score
from services.action_queue_service import _kev_sla, _pending_approvals
from services.policy_gate import evaluate_gate
from services.project_detail_service import _load_project, distributions_for_scan
from services.project_service import ProjectForbidden
from services.release_snapshot_service import _severity_distribution_by_scan

log = structlog.get_logger("project_governance.service")

# Points in the sparkline. Enough to show a direction, few enough that the
# band stays a band — the Releases table is where a full history belongs.
TREND_POINTS = 8


async def _recent_succeeded_scans(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
) -> list[tuple[uuid.UUID, datetime]]:
    """The last ``TREND_POINTS`` succeeded scans, oldest first.

    Superseded snapshots are included on purpose: they are exactly the
    history a trend is made of, and the Releases tab — which excludes them —
    is answering a different question. Only the *current* posture reads the
    live snapshot, and that comes from ``evaluate_gate`` and the overview
    aggregation, not from here.

    The series is therefore at the mercy of ``scan_retention``, which hard
    deletes superseded scans after its grace period. A project scanned on
    every push can show eight points this week and "not enough scans" the
    next without anything having happened to it.
    """
    stmt = (
        select(Scan.id, Scan.created_at)
        .where(Scan.project_id == project_id)
        .where(cast(Scan.status, String) == "succeeded")
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(TREND_POINTS)
    )
    rows = [(row[0], row[1]) for row in (await session.execute(stmt)).all()]
    return list(reversed(rows))


async def get_project_governance(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
) -> ProjectGovernance:
    """Build the governance band for one project.

    Read-only. Raises ``ProjectNotFound`` / ``ProjectForbidden``; the router
    maps both to RFC 7807 with the existence-hide contract.
    """
    # The owning module's loader, not a second copy of the same query — it
    # already raises ProjectNotFound for a missing row.
    project = await _load_project(session, project_id)

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_governance",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}",
        ),
    )

    gate_result = await evaluate_gate(session, project_id)
    current_scan_id = gate_result.scan_id

    trend_rows = await _recent_succeeded_scans(session, project_id=project_id)

    # The score comes from the Overview tab's own aggregation, not from a
    # second assembly of the same idea. The first version of this module built
    # it from the release-snapshot helpers, which start at `license_findings`
    # rather than `scan_components` — so every component with no licence row
    # vanished from the licence axis, and a project the tab scored 8.7 read 0
    # in the band three pixels above it. Sharing the query is what makes that
    # class of drift impossible rather than merely tested for.
    score = 0.0
    if current_scan_id:
        severity, licenses, _ = await distributions_for_scan(
            session, scan_id=current_scan_id
        )
        score = risk_score.compute_risk_score(severity, licenses)

    # The sparkline is a different question — one number per historical scan —
    # so it uses the batched per-scan aggregation. It is only ever compared
    # with itself.
    trend_severity = await _severity_distribution_by_scan(
        session, scan_ids=[scan_id for scan_id, _ in trend_rows]
    )

    # Independent of each other and of the score — no reason to pay for them
    # in series on a page-load path.
    kev, approvals = await asyncio.gather(
        _kev_sla(
            session,
            scan_ids=[current_scan_id] if current_scan_id else [],
            today=datetime.now(tz=UTC).date(),
        ),
        _pending_approvals(session, project_ids=[project_id]),
    )

    governance = ProjectGovernance(
        project_id=project_id,
        scanned=current_scan_id is not None,
        risk_score=score,
        gate=GovernanceGate(
            # `evaluate_gate` reports a verdict even with no scan to judge;
            # the band shows "never scanned" instead, so the status is only
            # carried when there is a snapshot behind it.
            status=gate_result.gate if current_scan_id else None,
            critical_cve_count=gate_result.critical_cve_count,
            forbidden_license_count=gate_result.forbidden_license_count,
            epss_gate_count=gate_result.epss_gate_count,
            malicious_component_count=gate_result.malicious_component_count,
            scan_id=current_scan_id,
        ),
        kev_sla=GovernanceKevSla(overdue=kev.overdue, due_soon=kev.due_soon),
        pending_approvals=approvals,
        trend=[
            GovernanceTrendPoint(
                scan_id=scan_id,
                scanned_at=scanned_at,
                critical=trend_severity.get(scan_id, {}).get("critical", 0),
            )
            for scan_id, scanned_at in trend_rows
        ],
    )

    log.info(
        "project_governance.built",
        project_id=str(project_id),
        scanned=governance.scanned,
        gate=governance.gate.status,
        trend_points=len(governance.trend),
    )
    return governance


__all__ = ["TREND_POINTS", "get_project_governance"]
