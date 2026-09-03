# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Dashboard summary service — portfolio overview aggregate (read-only).

Powers ``GET /v1/dashboard/summary``. The router is a thin shell; every DB read
and every authorization decision lives here.

Scoping (BOLA / IDOR — the most important contract in this module)
-----------------------------------------------------------------
All aggregates are restricted to the caller's *accessible projects*:

  - super-admin (``actor.is_superuser`` or ``actor.role == "super_admin"``)
    → every project in the deployment.
  - everyone else → only projects whose ``team_id`` is one of the actor's team
    memberships (``actor.team_roles`` keys).

We resolve the accessible *project ids* once, up front, and every subsequent
aggregate query is filtered by ``project_id IN (<accessible>)``. A caller with
no memberships (and not a super-admin) gets an all-zero summary without touching
the heavier tables. The per-team check uses ``actor.team_roles`` keys (the same
membership set ``core.authz.can_access_team`` consults), NOT ``actor.role`` —
``actor.role`` is the *highest* role across all memberships and says nothing
about which teams the actor belongs to (CWE-863 cross-team escalation).

"Latest succeeded scan per project"
-----------------------------------
``Project.latest_scan_id`` points at the most recent scan *regardless of
status*, so it cannot stand in for "latest *succeeded* scan". We compute the
latest succeeded scan id per accessible project with a single
``DISTINCT ON (project_id) ... ORDER BY project_id, created_at DESC`` query, then
aggregate vulnerability- and license-findings over exactly that set of scan ids.
Severity / license per component is the *worst* finding for that component within
its scan (MAX over a rank CASE), matching ``services.project_detail_service``.

Performance
-----------
A handful of independent aggregate queries (each ``func.count`` + ``group_by``)
rather than one giant join — no N+1. The finding aggregations anchor on
``vulnerability_findings.scan_id`` / ``license_findings.scan_id`` (both indexed)
restricted to the latest-succeeded scan-id set.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import String, case, cast, func, literal, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from core.security import CurrentUser
from models import (
    ComponentApproval,
    LicenseFinding,
    Project,
    Scan,
    Vulnerability,
    VulnerabilityFinding,
)
from models import (
    License as LicenseModel,
)
from schemas.dashboard import (
    DashboardSummary,
    LicenseCategoryCounts,
    ProjectLicenseCounts,
    ProjectSeverityCounts,
    RecentScan,
    ScanStatusCounts,
    VulnerabilitySeverityCounts,
)

log = structlog.get_logger("dashboard.service")


# ---------------------------------------------------------------------------
# Rank maps (severity / license) — mirror services.project_detail_service
# ---------------------------------------------------------------------------

# Higher rank = "worse". We pick the worst finding per component (MAX rank).
# `unknown` severity folds into `info` rank: a CVE whose severity we don't know
# must never render as a clean (none) ribbon.
_SEVERITY_FROM_RANK: dict[int, str] = {
    0: "none",
    1: "info",
    2: "low",
    3: "medium",
    4: "high",
    5: "critical",
}

# Persisted License.category → dashboard UI bucket. `forbidden` is surfaced as
# `prohibited` and `allowed` as `permissive`, matching the product's UI labels.
_LICENSE_RANK_TO_BUCKET: dict[int, str] = {
    0: "unknown",
    1: "permissive",
    2: "conditional",
    3: "prohibited",
}


#: Above this many ids the predicate below switches to a single array
#: parameter. asyncpg refuses a statement carrying more than 32 767
#: arguments; ``_accessible_project_ids`` returns EVERY project for a
#: super-admin, unpaginated, so this module is the one place in the codebase
#: where that ceiling is reachable from a single deployment's own data
#: (services.inventory_service._among_scans hit the same ceiling first, at
#: S8 concurrency-scaling; mirrors its threshold and shape).
# Same ranks as `_LICENSE_RANK_TO_BUCKET`, but keyed to the PERSISTED category
# names. The project-shaped chart deep-links into `/projects?license_category=`,
# which filters on what the database stores, so translating to the UI labels
# here would produce a segment whose own link returns nothing.
_PROJECT_LICENSE_FROM_RANK: dict[int, str] = {
    0: "unknown",
    1: "allowed",
    2: "conditional",
    3: "forbidden",
}


_ID_INLINE_LIMIT = 5_000


def _among(column: InstrumentedAttribute[uuid.UUID], ids: list[uuid.UUID]) -> ColumnElement[bool]:
    """ "This column's value is one of ``ids``", at any list size.

    Under ``_ID_INLINE_LIMIT`` this is a plain ``IN`` (one bind parameter per
    element), the fast path every deployment actually takes. Over it, the
    list is passed as ONE array parameter instead: asyncpg's 32 767-argument
    ceiling turned a super-admin's accessible-project count directly into
    request failures otherwise, per services.inventory_service._among_scans.
    """
    if len(ids) <= _ID_INLINE_LIMIT:
        return column.in_(ids)
    return column == func.any(literal(ids, ARRAY(PG_UUID(as_uuid=True))))


def _accessible_team_ids(actor: CurrentUser) -> list[uuid.UUID]:
    """The team ids whose projects the actor may read (non-super-admin path).

    Uses ``actor.team_roles`` keys — the actor's membership set — NOT
    ``actor.role`` (which is only the highest role across teams and would leak
    other teams' data if used as a membership signal).
    """
    return list(actor.team_roles.keys())


def _severity_rank_case() -> Any:
    """CASE mapping a ``vuln_severity`` ENUM value to its integer rank.

    Postgres ENUM ↔ varchar comparison needs an explicit cast to text, exactly
    as in ``services.project_detail_service._severity_rank_case``.
    """
    return case(
        {
            literal("critical"): 5,
            literal("high"): 4,
            literal("medium"): 3,
            literal("low"): 2,
            literal("info"): 1,
            literal("unknown"): 1,
        },
        value=cast(Vulnerability.severity, String),
        else_=0,
    )


def _license_rank_case() -> Any:
    """CASE mapping a ``license_category`` ENUM value to its integer rank."""
    return case(
        {
            literal("forbidden"): 3,
            literal("conditional"): 2,
            literal("allowed"): 1,
        },
        value=cast(LicenseModel.category, String),
        else_=0,
    )


async def _accessible_project_ids(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    include_archived: bool = False,
) -> list[uuid.UUID]:
    """All project ids the actor may read.

    super-admins get every project; everyone else is clamped to projects owned
    by a team in their membership set. By default archived projects are
    excluded (the headline ``project_count`` is non-archived projects); finding
    aggregations also ride this list so an archived project never contributes.
    """
    is_super = actor.is_superuser or actor.role == "super_admin"

    stmt = select(Project.id)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))

    if not is_super:
        team_ids = _accessible_team_ids(actor)
        if not team_ids:
            return []
        stmt = stmt.where(Project.team_id.in_(team_ids))

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _latest_succeeded_scan_ids(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """The id of the latest *succeeded* scan for each given project.

    ``Project.latest_scan_id`` tracks the most recent scan of ANY status, so we
    cannot reuse it for finding aggregation. One ``DISTINCT ON`` pass gives the
    newest succeeded scan per project.

    The tie-break after ``created_at`` is ``completed_at``, not the scan's id.
    ``created_at`` defaults to Postgres ``now()`` (transaction time), so two
    scans created in the same transaction share the identical value, not just
    a rare microsecond coincidence; the id is a random UUID with no relation to
    insertion order. ``completed_at`` is stamped by ``mark_succeeded`` with a
    real, per-scan ``datetime.now(UTC)`` reading at the moment each scan
    actually finished, which two different scans essentially never share.
    """
    if not project_ids:
        return []

    stmt = (
        select(Scan.id)
        .distinct(Scan.project_id)
        .where(_among(Scan.project_id, project_ids))
        .where(cast(Scan.status, String) == "succeeded")
        .order_by(
            Scan.project_id,
            Scan.created_at.desc(),
            Scan.completed_at.desc(),
            Scan.id.desc(),
        )
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _scan_status_counts(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> ScanStatusCounts:
    """Scan counts grouped by status over the accessible projects."""
    counts = ScanStatusCounts()
    if not project_ids:
        return counts

    stmt = (
        select(cast(Scan.status, String).label("status"), func.count().label("n"))
        .where(_among(Scan.project_id, project_ids))
        .group_by(cast(Scan.status, String))
    )
    result = await session.execute(stmt)
    by_status = {row.status: int(row.n) for row in result.all()}
    return ScanStatusCounts(
        queued=by_status.get("queued", 0),
        running=by_status.get("running", 0),
        succeeded=by_status.get("succeeded", 0),
        failed=by_status.get("failed", 0),
    )


async def _severity_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> VulnerabilitySeverityCounts:
    """Component findings by worst severity over the latest-succeeded scans.

    One component-version inside one scan can have many CVE findings; we collapse
    to the *worst* (MAX rank) per (scan, component_version), then count the
    components landing in each severity bucket. Components with no CVE finding
    are not counted (they contribute to neither severity nor — necessarily —
    license; the dashboard severity widget is "components carrying a finding").
    """
    counts = VulnerabilitySeverityCounts()
    if not scan_ids:
        return counts

    sev_rank = _severity_rank_case()
    per_cv = (
        select(
            VulnerabilityFinding.scan_id.label("scan_id"),
            VulnerabilityFinding.component_version_id.label("cv_id"),
            func.max(sev_rank).label("max_rank"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(_among(VulnerabilityFinding.scan_id, scan_ids))
        .group_by(
            VulnerabilityFinding.scan_id,
            VulnerabilityFinding.component_version_id,
        )
        .subquery()
    )

    stmt = select(per_cv.c.max_rank, func.count().label("n")).group_by(per_cv.c.max_rank)
    result = await session.execute(stmt)

    buckets: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for row in result.all():
        bucket = _SEVERITY_FROM_RANK.get(int(row.max_rank), "none")
        if bucket == "none":
            # rank 0 means no severity resolved for this finding set; skip.
            continue
        buckets[bucket] = buckets.get(bucket, 0) + int(row.n)
    return VulnerabilitySeverityCounts(**buckets)


async def _license_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> LicenseCategoryCounts:
    """Component license verdicts (worst category) over the latest-succeeded scans."""
    counts = LicenseCategoryCounts()
    if not scan_ids:
        return counts

    lic_rank = _license_rank_case()
    per_cv = (
        select(
            LicenseFinding.scan_id.label("scan_id"),
            LicenseFinding.component_version_id.label("cv_id"),
            func.max(lic_rank).label("max_rank"),
        )
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .where(_among(LicenseFinding.scan_id, scan_ids))
        .group_by(
            LicenseFinding.scan_id,
            LicenseFinding.component_version_id,
        )
        .subquery()
    )

    stmt = select(per_cv.c.max_rank, func.count().label("n")).group_by(per_cv.c.max_rank)
    result = await session.execute(stmt)

    buckets: dict[str, int] = {
        "prohibited": 0,
        "conditional": 0,
        "permissive": 0,
        "unknown": 0,
    }
    for row in result.all():
        bucket = _LICENSE_RANK_TO_BUCKET.get(int(row.max_rank), "unknown")
        buckets[bucket] = buckets.get(bucket, 0) + int(row.n)
    return LicenseCategoryCounts(**buckets)


async def _project_severity_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> ProjectSeverityCounts:
    """Projects bucketed by their single worst finding, over the latest scans.

    The dashboard chart this feeds counts projects, not components, and each
    segment deep-links to ``/projects?severity=...``. Computing it in the
    client off one page of projects is what made the chart wrong above 100
    projects, and wrong in the dangerous direction: the page is ordered by
    ``updated_at DESC``, so the projects that fall off the end are the ones
    nobody has touched in a while, which is where risk accumulates.

    A project whose latest succeeded scan carries no finding lands in
    ``none``. A project with no succeeded scan lands in no bucket at all: we
    have not looked at it, which is not the same as finding it clean.
    """
    counts = ProjectSeverityCounts()
    if not scan_ids:
        return counts

    sev_rank = _severity_rank_case()
    per_project = (
        select(
            Scan.project_id.label("project_id"),
            func.max(sev_rank).label("max_rank"),
        )
        .select_from(Scan)
        .outerjoin(
            VulnerabilityFinding,
            VulnerabilityFinding.scan_id == Scan.id,
        )
        .outerjoin(
            Vulnerability,
            Vulnerability.id == VulnerabilityFinding.vulnerability_id,
        )
        .where(_among(Scan.id, scan_ids))
        .group_by(Scan.project_id)
        .subquery()
    )

    stmt = select(per_project.c.max_rank, func.count().label("n")).group_by(per_project.c.max_rank)
    result = await session.execute(stmt)

    buckets: dict[str, int] = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "info": 0,
        "none": 0,
    }
    for row in result.all():
        # A scan with no finding at all produces a NULL max, not 0: the outer
        # join leaves nothing for the CASE to evaluate. Both mean "clean".
        rank = 0 if row.max_rank is None else int(row.max_rank)
        buckets[_SEVERITY_FROM_RANK.get(rank, "none")] += int(row.n)
    return ProjectSeverityCounts(**buckets)


async def _project_license_counts(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> ProjectLicenseCounts:
    """Projects bucketed by their worst license verdict, over the latest scans.

    Buckets carry the persisted category names rather than the UI labels used
    by ``LicenseCategoryCounts``, because this chart's segments deep-link to
    ``/projects?license_category=...`` and that filter matches persisted
    values.
    """
    counts = ProjectLicenseCounts()
    if not scan_ids:
        return counts

    lic_rank = _license_rank_case()
    per_project = (
        select(
            Scan.project_id.label("project_id"),
            func.max(lic_rank).label("max_rank"),
        )
        .select_from(Scan)
        .outerjoin(LicenseFinding, LicenseFinding.scan_id == Scan.id)
        .outerjoin(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .where(_among(Scan.id, scan_ids))
        .group_by(Scan.project_id)
        .subquery()
    )

    stmt = select(per_project.c.max_rank, func.count().label("n")).group_by(per_project.c.max_rank)
    result = await session.execute(stmt)

    buckets: dict[str, int] = {
        "forbidden": 0,
        "conditional": 0,
        "allowed": 0,
        "unknown": 0,
    }
    for row in result.all():
        rank = 0 if row.max_rank is None else int(row.max_rank)
        buckets[_PROJECT_LICENSE_FROM_RANK.get(rank, "unknown")] += int(row.n)
    return ProjectLicenseCounts(**buckets)


async def _pending_approvals_count(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
) -> int:
    """Open component approvals (pending or under_review) for accessible projects.

    The ``approval_status`` enum's two *open* states are ``pending`` and
    ``under_review`` (see ``models.component_approval.APPROVAL_STATUS_VALUES``);
    ``approved`` / ``rejected`` are terminal. We count both open states because
    each represents a component still awaiting a decision.
    """
    if not project_ids:
        return 0

    stmt = (
        select(func.count())
        .select_from(ComponentApproval)
        .where(_among(ComponentApproval.project_id, project_ids))
        .where(cast(ComponentApproval.status, String).in_(("pending", "under_review")))
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def _recent_scans(
    session: AsyncSession,
    *,
    project_ids: list[uuid.UUID],
    limit: int = 10,
) -> list[RecentScan]:
    """The most recent scans across accessible projects, newest first."""
    if not project_ids:
        return []

    stmt = (
        select(
            Scan.id.label("scan_id"),
            Scan.project_id.label("project_id"),
            Project.name.label("project_name"),
            cast(Scan.status, String).label("status"),
            cast(Scan.kind, String).label("kind"),
            Scan.completed_at.label("finished_at"),
            # Feature #18 Part A — the release/version label rides inside the
            # scan metadata blob (the canonical store); we read it back here so
            # the dashboard feed can map a release → scan without a second query.
            Scan.scan_metadata.label("scan_metadata"),
        )
        .join(Project, Project.id == Scan.project_id)
        .where(_among(Scan.project_id, project_ids))
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [
        RecentScan(
            scan_id=row.scan_id,
            project_id=row.project_id,
            project_name=row.project_name,
            status=row.status,
            kind=row.kind,
            finished_at=row.finished_at,
            release=_release_from_metadata(row.scan_metadata),
        )
        for row in result.all()
    ]


def _release_from_metadata(metadata: Any) -> str | None:
    """Extract the release/version label from a scan's metadata blob (#18 Part A).

    Returns ``None`` for a missing / non-string / blank value so a row written
    before this feature (or a malformed metadata blob) never surfaces a non-string
    release on the dashboard feed.
    """
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("release")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


async def get_dashboard_summary(
    session: AsyncSession,
    *,
    actor: CurrentUser,
) -> DashboardSummary:
    """Build the portfolio-overview summary for ``actor``.

    Read-only. Every aggregate is scoped to the actor's accessible projects (see
    module docstring). Returns an all-zero, empty summary when the actor has no
    accessible projects — never raises for the empty case.
    """
    project_ids = await _accessible_project_ids(session, actor=actor)

    if not project_ids:
        log.info("dashboard.summary.empty", actor_id=str(actor.id))
        return DashboardSummary(
            project_count=0,
            pending_approvals_count=0,
        )

    latest_scan_ids = await _latest_succeeded_scan_ids(session, project_ids=project_ids)

    scan_status_counts = await _scan_status_counts(session, project_ids=project_ids)
    severity_counts = await _severity_counts(session, scan_ids=latest_scan_ids)
    license_counts = await _license_counts(session, scan_ids=latest_scan_ids)
    project_severity = await _project_severity_counts(session, scan_ids=latest_scan_ids)
    project_license = await _project_license_counts(session, scan_ids=latest_scan_ids)
    pending = await _pending_approvals_count(session, project_ids=project_ids)
    recent = await _recent_scans(session, project_ids=project_ids)

    log.info(
        "dashboard.summary",
        actor_id=str(actor.id),
        project_count=len(project_ids),
        latest_scan_count=len(latest_scan_ids),
    )

    return DashboardSummary(
        project_count=len(project_ids),
        scan_status_counts=scan_status_counts,
        vulnerability_severity_counts=severity_counts,
        license_category_counts=license_counts,
        project_severity_counts=project_severity,
        project_license_counts=project_license,
        pending_approvals_count=pending,
        recent_scans=recent,
    )


__all__ = ["get_dashboard_summary"]
