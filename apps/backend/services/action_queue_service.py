# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""``/v1/dashboard/action-queue`` — what needs a person, right now.

Why this is not a list of gate verdicts
---------------------------------------
The obvious implementation calls :func:`services.policy_gate.evaluate_gate`
once per project. That costs five to seven queries each — latest succeeded
scan, critical reachability counts, the owning team, its effective licence
policy, forbidden-licence components, EPSS — so a portfolio view would issue
hundreds of round trips to render one panel.

The two obvious fixes are both wrong. Stamping the verdict onto the scan row
and caching the aggregate share the same defect: the gate's inputs are
mutable after the scan finishes. Triage moves a finding to ``not_affected``,
and an admin edits the team's licence policy. A stored verdict would keep
reporting "blocked" after the blocking CVE was dismissed, and the invalidation
surface needed to prevent that is larger than the problem.

So this module aggregates the gate's *inputs* rather than its output — open
critical findings, forbidden-licence components, EPSS-gated findings — grouped
across every accessible project, with the same threshold applied on top.
Always live, and the number of queries does not grow with the portfolio.

One input resists grouping outright. A team licence policy can override an
allowed licence to forbidden, waive a forbidden one, or decide what an
unrecognised licence counts as, and none of that is visible in the stored
``License.category``. Policies resolve per team, so they are looked up once
per distinct team, and only scans in a policy-enabled team pay for the
per-scan evaluator at all. Re-classifying those scans is still per-scan CPU
work (the memoised evaluator in ``services.policy_gate`` runs once per scan's
distinct license expressions), but it is loaded with one batched query for
every affected scan in the request (``policy_gate.load_scan_license_rows_batch``,
``load_flagged_purls_batch`` for the malicious axis), not one query per scan.
A portfolio with many projects in one policy-enabled team used to pay a query
per project here; it now pays one query for the whole team regardless of how
many projects it has. Query *cost* still grows with the portfolio, since
these aggregates read every open finding in scope, which is why the route
stays rate limited per actor.

The cost of all this is that the blocking rule now exists in two places, which
is exactly the duplicated-vocabulary trap CLAUDE.md hardening rule #2 is
about. ``tests/integration/test_action_queue_gate_parity.py`` is what pays
for it: it runs both paths over the same rows and fails on disagreement.

That test is only worth as much as the states it reaches. Its first version
built every team without a policy, so both paths took the identical static
branch — it proved the two agree where they *cannot* differ, while a
policy-blocked project was silently missing from the panel whose job is to
list blocked builds. An independent security review found that; the matrix
now covers policy overrides, waivers, and the EPSS-only path.

Team isolation rides ``services.dashboard_service._accessible_project_ids``,
the same helper the summary endpoint uses, so there is one definition of
"projects this caller may see" rather than a second one here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import structlog
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import (
    ComponentApproval,
    ComponentVersion,
    License,
    LicenseFinding,
    Project,
    Scan,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from models.license_policy import LicensePolicy
from schemas.action_queue import (
    ActionQueue,
    GateBlockedProject,
    KevSlaBucket,
    StaleProject,
)
from services.dashboard_service import (
    _accessible_project_ids,
    _latest_succeeded_scan_ids,
)
from services.license_policy_service import get_effective_policy
from services.malicious import malicious_catalog
from services.policy_gate import (
    _CLOSED_FINDING_STATUSES,
    _active_malicious_waivers,
    _categorize_license_rows,
    _resolve_epss_threshold,
    load_flagged_purls_batch,
    load_scan_license_rows_batch,
)

logger = structlog.get_logger("action_queue.service")

# A project with no successful scan in this long is not being watched, whatever
# its last verdict said. Two weeks is long enough that a normal fortnightly
# cadence does not trip it and short enough that a forgotten project surfaces
# inside a sprint.
STALE_SCAN_DAYS = 14

# How far ahead a KEV remediation deadline counts as "imminent". CISA due dates
# are absolute, so the useful question is "is this close enough that starting
# now still leaves time", not "how long has it been open".
KEV_DUE_SOON_DAYS = 7

# Rows returned per bucket. The queue is a prompt to act, not a work list —
# past a handful of entries the user should be on the dedicated page, and an
# unbounded list would let one noisy project crowd out every other signal.
BUCKET_LIMIT = 10


async def _gate_input_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """Open critical findings and forbidden-licence components, per scan.

    Two grouped queries for the whole portfolio, mirroring what
    ``policy_gate`` computes per project. ``_CLOSED_FINDING_STATUSES`` is
    imported from that module rather than restated, so a change to what counts
    as closed cannot land on one side only.
    """
    if not scan_ids:
        return {}, {}

    # Severity lives on the vulnerability, not the finding — the same join
    # `policy_gate._critical_reachability_counts` makes. Reading a `severity`
    # column off the finding would silently count nothing.
    critical_stmt = (
        select(VulnerabilityFinding.scan_id, func.count())
        .select_from(VulnerabilityFinding)
        .join(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .where(VulnerabilityFinding.scan_id.in_(scan_ids))
        .where(cast(Vulnerability.severity, String) == "critical")
        .where(cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES))
        .group_by(VulnerabilityFinding.scan_id)
    )
    critical = {
        row[0]: int(row[1]) for row in (await session.execute(critical_stmt)).all()
    }

    # Distinct component versions, not licence-finding rows: one component
    # carrying two forbidden licences is one blocked component, which is what
    # the gate counts.
    forbidden_stmt = (
        select(
            LicenseFinding.scan_id,
            func.count(func.distinct(LicenseFinding.component_version_id)),
        )
        .join(License, License.id == LicenseFinding.license_id)
        .where(LicenseFinding.scan_id.in_(scan_ids))
        .where(cast(License.category, String) == "forbidden")
        .group_by(LicenseFinding.scan_id)
    )
    forbidden = {
        row[0]: int(row[1]) for row in (await session.execute(forbidden_stmt)).all()
    }

    return critical, forbidden


async def _malicious_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Components each scan carries that the malicious snapshot flags.

    A project can be blocked by this term alone — the gate fails on any
    non-empty reason clause, and this is one of five. Omitting it here would
    drop exactly those builds from the panel whose whole purpose is listing
    blocked builds, and the most urgent ones at that: a malicious package is
    an attack in the build, not a defect to schedule.

    Raw tally: waivers are subtracted afterwards by
    ``_malicious_counts_under_policy``, which reuses the gate's own resolver
    rather than restating the rule here.
    """
    if not scan_ids:
        return {}

    stmt = (
        select(
            ScanComponent.scan_id,
            func.count(func.distinct(ScanComponent.component_version_id)),
        )
        .select_from(ScanComponent)
        .join(
            ComponentVersion,
            ComponentVersion.id == ScanComponent.component_version_id,
        )
        .where(ScanComponent.scan_id.in_(scan_ids))
        .where(ComponentVersion.malicious_state == "flagged")
        .group_by(ScanComponent.scan_id)
    )
    return {sid: int(count) for sid, count in (await session.execute(stmt)).all()}


async def _epss_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """Open findings whose CVE scores at or above the EPSS gate threshold.

    A project can be blocked by this term alone — `evaluate_gate` fails on any
    non-empty reason clause, and the EPSS clause is one of three. Leaving it
    out meant an operator who had configured `GATE_EPSS_THRESHOLD` got a panel
    that omitted exactly the builds their own configuration was failing.

    When the threshold is unset the gate condition is disabled, so this costs
    nothing: no query is issued at all.
    """
    threshold = _resolve_epss_threshold()
    if threshold is None or not scan_ids:
        return {}

    stmt = (
        select(VulnerabilityFinding.scan_id, func.count())
        .select_from(VulnerabilityFinding)
        .join(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .where(VulnerabilityFinding.scan_id.in_(scan_ids))
        .where(Vulnerability.epss_score >= threshold)
        .where(cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES))
        .group_by(VulnerabilityFinding.scan_id)
    )
    return {row[0]: int(row[1]) for row in (await session.execute(stmt)).all()}


async def _forbidden_counts_under_policy(
    session: AsyncSession,
    *,
    scan_by_project: dict[uuid.UUID, uuid.UUID],
    static_counts: dict[uuid.UUID, int],
) -> dict[uuid.UUID, int]:
    """Re-count forbidden components for teams that run a licence policy.

    The catalogue category a licence was stored with is not the last word. A
    team policy can override an allowed licence to forbidden, waive a
    forbidden one, or decide what an unrecognised licence counts as — and
    `evaluate_gate` honours all three. A grouped read of `License.category`
    honours none of them, so on a policy-enabled team the two answers diverge
    in both directions: a build the gate blocks goes missing from the panel
    whose job is to list blocked builds, and a waived project keeps being
    listed until people stop reading the panel.

    Policies resolve per team, not per project, so the lookup is bounded by
    team count. Only scans in a team that actually has an enabled policy pay
    for the dynamic re-classification; everything else keeps the grouped
    result it already has, which is the common case.

    The re-classification itself no longer costs a query per scan. Every scan
    that needs it is collected first, then loaded in ONE query
    (``policy_gate.load_scan_license_rows_batch``) regardless of how many
    projects the policy-enabled team has, replacing the query-per-project
    loop that used to make the panel's cost track the portfolio's project
    count, not its team count.
    """
    if not scan_by_project:
        return static_counts

    team_rows = (
        await session.execute(
            select(Project.id, Project.team_id).where(
                Project.id.in_(list(scan_by_project))
            )
        )
    ).all()

    policies: dict[uuid.UUID, LicensePolicy | None] = {}
    counts = dict(static_counts)

    # Scans whose owning team runs an enabled policy: the only ones that need
    # the dynamic recount. Collected first so the row load below is one query
    # for the whole batch.
    scan_policy: dict[uuid.UUID, LicensePolicy] = {}
    for project_id, team_id in team_rows:
        if team_id is None:
            continue
        if team_id not in policies:
            policies[team_id] = await get_effective_policy(session, team_id=team_id)
        policy = policies[team_id]
        if policy is None:
            continue
        scan_policy[scan_by_project[project_id]] = policy

    if not scan_policy:
        return counts

    rows_by_scan = await load_scan_license_rows_batch(session, list(scan_policy))
    for scan_id, policy in scan_policy.items():
        verdicts = _categorize_license_rows(rows_by_scan.get(scan_id, []), policy)
        counts[scan_id] = sum(1 for v in verdicts.values() if v.category == "forbidden")

    return counts


async def _malicious_counts_under_policy(
    session: AsyncSession,
    *,
    scan_by_project: dict[uuid.UUID, uuid.UUID],
    raw_counts: dict[uuid.UUID, int],
) -> dict[uuid.UUID, int]:
    """Subtract active waivers so the panel agrees with the gate.

    A waived component does not block, so listing its project under "blocked
    builds" is the failure this module's docstring already describes for the
    licence axis: the panel keeps naming a project until people stop reading
    the panel.

    No waiver rule is duplicated here — the resolution is
    ``policy_gate._active_malicious_waivers``, reused. Only teams that run a
    policy pay for the extra lookup, and a team with no malicious waivers
    exits after one dict miss.

    The purl lookup that follows a waiver hit is one query for every affected
    scan (``policy_gate.load_flagged_purls_batch``), not one query per scan:
    the same batching :func:`_forbidden_counts_under_policy` applies to the
    licence axis, for the same reason. A policy-enabled team's cost here used
    to track its project count.
    """
    if not raw_counts or not scan_by_project:
        return raw_counts

    now = datetime.now(tz=UTC)
    team_rows = (
        await session.execute(
            select(Project.id, Project.team_id).where(
                Project.id.in_(list(scan_by_project))
            )
        )
    ).all()

    policies: dict[uuid.UUID, LicensePolicy | None] = {}
    counts = dict(raw_counts)

    # Scans that (a) belong to a policy-bearing team and (b) have a nonzero
    # raw malicious count (a scan with nothing flagged has nothing to waive).
    # Collected first so the purl lookup below is one query for the batch.
    scan_waived: dict[uuid.UUID, frozenset[str]] = {}
    for project_id, team_id in team_rows:
        scan_id = scan_by_project.get(project_id)
        if scan_id is None or not counts.get(scan_id):
            continue
        if team_id is None:
            continue
        if team_id not in policies:
            policies[team_id] = await get_effective_policy(session, team_id=team_id)
        waived = _active_malicious_waivers(policies[team_id], now)
        if not waived:
            continue
        scan_waived[scan_id] = waived

    if not scan_waived:
        return counts

    purls_by_scan = await load_flagged_purls_batch(session, list(scan_waived))
    for scan_id, waived in scan_waived.items():
        purls = purls_by_scan.get(scan_id, [])
        counts[scan_id] = sum(
            1 for purl in purls if malicious_catalog.base_purl(purl) not in waived
        )

    return counts


async def _gate_blocked(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
    scan_ids: list[uuid.UUID],
) -> list[GateBlockedProject]:
    """Projects whose latest succeeded scan trips the build gate."""
    if not scan_ids:
        return []

    critical, forbidden = await _gate_input_counts(session, scan_ids=scan_ids)

    scan_by_project = {
        project_id: scan_id
        for scan_id, project_id in (
            await session.execute(
                select(Scan.id, Scan.project_id).where(Scan.id.in_(scan_ids))
            )
        ).all()
    }
    forbidden = await _forbidden_counts_under_policy(
        session, scan_by_project=scan_by_project, static_counts=forbidden
    )

    epss = await _epss_counts(session, scan_ids=scan_ids)
    malicious = await _malicious_counts(session, scan_ids=scan_ids)
    malicious = await _malicious_counts_under_policy(
        session, scan_by_project=scan_by_project, raw_counts=malicious
    )

    blocked_scan_ids = [
        sid
        for sid in scan_ids
        if (
            critical.get(sid, 0)
            or forbidden.get(sid, 0)
            or epss.get(sid, 0)
            or malicious.get(sid, 0)
        )
    ]
    if not blocked_scan_ids:
        return []

    rows = (
        await session.execute(
            select(Scan.id, Scan.project_id, Project.name)
            .join(Project, Project.id == Scan.project_id)
            .where(Scan.id.in_(blocked_scan_ids))
            # Redundant today — blocked_scan_ids descends from a scoped query
            # — but it keeps the isolation inside the statement that reads the
            # rows, rather than two calls away where a later edit could widen
            # the id list without noticing.
            .where(Scan.project_id.in_(project_ids))
        )
    ).all()

    blocked = [
        GateBlockedProject(
            project_id=project_id,
            project_name=name,
            scan_id=scan_id,
            critical_cve_count=critical.get(scan_id, 0),
            forbidden_license_count=forbidden.get(scan_id, 0),
            epss_gate_count=epss.get(scan_id, 0),
            malicious_component_count=malicious.get(scan_id, 0),
        )
        for scan_id, project_id, name in rows
    ]
    # Worst first, then by name. The name is not cosmetic: without a
    # tie-break, two projects with equal totals swap places between calls and
    # the one dropped at the BUCKET_LIMIT boundary changes at random.
    blocked.sort(
        key=lambda b: (
            -(
                b.critical_cve_count
                + b.forbidden_license_count
                + b.epss_gate_count
                + b.malicious_component_count
            ),
            b.project_name,
        )
    )
    return blocked[:BUCKET_LIMIT]


async def _blocked_for_projects(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> list[GateBlockedProject]:
    """Resolve the latest succeeded scans, then evaluate the gate bucket.

    The request path threads scan ids through so the resolution happens once.
    Callers that hold only project ids — the parity tests, chiefly — get this
    wrapper rather than reaching for the private pair, so a change to how
    scans are resolved cannot make the tests and the endpoint disagree about
    which scan they are talking about.
    """
    scan_ids = await _latest_succeeded_scan_ids(session, project_ids=project_ids)
    return await _gate_blocked(session, project_ids=project_ids, scan_ids=scan_ids)


async def _kev_sla(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
    today: date,
) -> KevSlaBucket:
    """Open KEV findings past, or close to, their CISA remediation date.

    Counted over the latest succeeded scan per project so a superseded scan's
    findings do not double-count, and restricted to open statuses because a
    dismissed finding is not work.
    """
    if not scan_ids:
        return KevSlaBucket(overdue=0, due_soon=0)

    # `kev` / `kev_due_date` live on the vulnerability, alongside `severity` —
    # the finding row carries triage state, not the CVE's own attributes.
    base = (
        select(func.count())
        .select_from(VulnerabilityFinding)
        .join(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .where(VulnerabilityFinding.scan_id.in_(scan_ids))
        .where(Vulnerability.kev.is_(True))
        .where(Vulnerability.kev_due_date.is_not(None))
        .where(cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES))
    )

    overdue = int(
        (await session.execute(base.where(Vulnerability.kev_due_date < today)))
        .scalar_one()
    )
    due_soon = int(
        (
            await session.execute(
                base.where(Vulnerability.kev_due_date >= today).where(
                    Vulnerability.kev_due_date
                    <= today + timedelta(days=KEV_DUE_SOON_DAYS)
                )
            )
        ).scalar_one()
    )
    return KevSlaBucket(overdue=overdue, due_soon=due_soon)


async def _stale_projects(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
    now: datetime,
) -> list[StaleProject]:
    """Projects with no successful scan inside ``STALE_SCAN_DAYS``.

    A project that has never succeeded counts as stale from its creation, not
    from never — otherwise a project registered and then forgotten stays
    invisible precisely because nothing ever ran on it.
    """
    if not project_ids:
        return []

    cutoff = now - timedelta(days=STALE_SCAN_DAYS)
    last_success = (
        select(
            Scan.project_id.label("project_id"),
            func.max(Scan.created_at).label("last_at"),
        )
        .where(Scan.project_id.in_(project_ids))
        .where(cast(Scan.status, String) == "succeeded")
        .group_by(Scan.project_id)
        .subquery()
    )

    rows = (
        await session.execute(
            select(Project.id, Project.name, last_success.c.last_at)
            .outerjoin(last_success, last_success.c.project_id == Project.id)
            .where(Project.id.in_(project_ids))
            .where(
                (last_success.c.last_at.is_(None) & (Project.created_at < cutoff))
                | (last_success.c.last_at < cutoff)
            )
            .order_by(last_success.c.last_at.asc().nullsfirst(), Project.name)
            .limit(BUCKET_LIMIT)
        )
    ).all()

    return [
        StaleProject(
            project_id=project_id,
            project_name=name,
            last_succeeded_at=last_at,
        )
        for project_id, name, last_at in rows
    ]


async def _pending_approvals(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> int:
    if not project_ids:
        return 0
    stmt = (
        select(func.count())
        .select_from(ComponentApproval)
        .where(ComponentApproval.project_id.in_(project_ids))
        .where(cast(ComponentApproval.status, String).in_(("pending", "under_review")))
    )
    return int((await session.execute(stmt)).scalar_one())


async def get_action_queue(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    now: datetime | None = None,
) -> ActionQueue:
    """Everything waiting on a person, scoped to the caller's projects.

    ``now`` is injectable so tests can pin the clock rather than seed data
    relative to wall time — a date-boundary bug that only appears near
    midnight is not something to discover in production.
    """
    moment = now or datetime.now(tz=UTC)
    project_ids = await _accessible_project_ids(session, actor=actor)
    # Resolved once and threaded through. Both the gate and the KEV bucket
    # read the latest succeeded scan per project, and each computing it
    # separately meant the same DISTINCT ON over the whole portfolio ran
    # twice per request for nothing.
    scan_ids = await _latest_succeeded_scan_ids(session, project_ids=project_ids)

    queue = ActionQueue(
        pending_approvals=await _pending_approvals(session, project_ids=project_ids),
        kev_sla=await _kev_sla(session, scan_ids=scan_ids, today=moment.date()),
        gate_blocked=await _gate_blocked(
            session, project_ids=project_ids, scan_ids=scan_ids
        ),
        stale_projects=await _stale_projects(
            session, project_ids=project_ids, now=moment
        ),
    )
    logger.info(
        "action_queue.built",
        project_count=len(project_ids),
        pending_approvals=queue.pending_approvals,
        gate_blocked=len(queue.gate_blocked),
        stale=len(queue.stale_projects),
    )
    return queue


__all__ = [
    "BUCKET_LIMIT",
    "KEV_DUE_SOON_DAYS",
    "STALE_SCAN_DAYS",
    "get_action_queue",
]
