"""
Release-snapshot listing service — feature #28 Phase 1 (read-only).

Powers ``GET /v1/projects/{id}/releases``: the project's *succeeded* scans
(most-recent first), each row a release snapshot with a per-scan summary
(``release`` label, ``risk_score``, ``severity_summary``, ``gate_status``,
``component_count``). Diff / compare between releases is a LATER phase and is
intentionally absent here.

Why "succeeded scans only"
--------------------------
Each succeeded scan is an immutable snapshot of the project's SCA posture
(``scan_components`` / ``vulnerability_findings`` / ``license_findings`` keyed by
``scan_id``). A queued / running / failed / cancelled scan has no stable findings
to summarise, so the Releases table lists ONLY ``status='succeeded'`` scans —
the same population every current-state reader anchors on (see
``services.scan_resolution``).

Efficiency (no N+1)
-------------------
Succeeded scans per project are few, but we still avoid per-row queries:

  1. one paged query resolves the page's succeeded scan ids + their
     ``created_at`` + ``metadata.release`` (newest-first) and the total count;
  2. ONE grouped severity aggregation over the whole page's scan-id set →
     ``{scan_id: {critical, high, medium, low, ...}}`` (worst CVE per component,
     mirroring ``services.dashboard_service`` / ``project_list_enrichment``);
  3. ONE grouped license aggregation over the same set → per-scan license
     distribution (needed for the risk score's license weights);
  4. ONE grouped component-count aggregation over the same set.

The build-gate verdict is evaluated per scan via ``services.policy_gate``'s
``evaluate_gate(..., scan_id=...)`` (it has its own per-scan reachability /
license-policy logic that is not trivially batchable, and the page is small —
typically a handful of releases). Each call is a constant number of indexed
reads on exactly that one scan, so the whole endpoint is O(page_size) round
trips on the gate path and O(1) on the aggregation path.

Authorization
-------------
Mirrors ``services.project_detail_service.get_project_overview``: load the
project, then ``assert_team_access`` (super_admin bypasses; a non-member raises
``ProjectForbidden`` → 403; a missing project raises ``ProjectNotFound`` → 404).
The router translates both to RFC 7807. This is the same convention the other
project-detail read endpoints use.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import String, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from models import (
    License as LicenseModel,
)
from models import (
    LicenseFinding,
    Project,
    Scan,
    Vulnerability,
    VulnerabilityFinding,
)
from services import risk_score
from services.policy_gate import evaluate_gate
from services.project_service import ProjectForbidden, ProjectNotFound

log = structlog.get_logger("release_snapshot.service")


# Mirror services.project_detail_service so the Releases table can never disagree
# with the Overview tab on how a CVE severity / license category maps to a bucket.
_SEVERITY_FROM_RANK: dict[int, str] = {
    0: "none",
    1: "info",
    2: "low",
    3: "medium",
    4: "high",
    5: "critical",
}
_LICENSE_CATEGORY_FROM_RANK: dict[int, str] = {
    0: "unknown",
    1: "allowed",
    2: "conditional",
    3: "forbidden",
}

# The four risk-bearing buckets surfaced on a release row (info / none are not
# actionable). Matches schemas.release_snapshot.ReleaseSeveritySummary.
_SUMMARY_BUCKETS = ("critical", "high", "medium", "low")

# Per-snapshot risk_score uses the shared scorer (services.risk_score) so the
# Releases table can never disagree with the Overview tab for the same scan.
# The snapshot stores the single overall = max(security, license) figure.


def _severity_rank_case() -> Any:
    """CASE mapping a ``vuln_severity`` ENUM value to its integer rank.

    Postgres ENUM ↔ varchar comparison needs an explicit cast to text, exactly
    as in ``dashboard_service`` / ``project_detail_service``.
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


def _compute_risk_score(
    severity_distribution: dict[str, int],
    license_distribution: dict[str, int],
) -> float:
    """Overall (worst-axis) risk for one snapshot — delegates to the shared
    scorer so the Releases table matches the Overview tab. Imported by
    ``project_diff_service`` for the base/target risk deltas."""
    return risk_score.compute_risk_score(severity_distribution, license_distribution)


async def _paged_succeeded_scans(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    page: int,
    size: int,
) -> tuple[list[Scan], int]:
    """Return ``(page_of_succeeded_scans, total)`` newest-first.

    One query for the page (ordered ``created_at DESC, id DESC`` — stable across
    pages and covered by ``ix_scans_project_created_at``) and one count. We load
    the ORM ``Scan`` rows so we can read ``scan_metadata['release']`` without a
    second round-trip.
    """
    offset = (page - 1) * size

    # scan-retention: hide superseded snapshots. A superseded scan lost its ref
    # slot to a newer winner (and carries no release label — retire never
    # supersedes a release-labelled scan), so the Releases table lists only the
    # live ref snapshots + tagged releases.
    items_stmt = (
        select(Scan)
        .where(Scan.project_id == project_id)
        .where(cast(Scan.status, String) == "succeeded")
        .where(Scan.superseded_at.is_(None))
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(size)
        .offset(offset)
    )
    count_stmt = (
        select(func.count())
        .select_from(Scan)
        .where(Scan.project_id == project_id)
        .where(cast(Scan.status, String) == "succeeded")
        .where(Scan.superseded_at.is_(None))
    )

    items_result = await session.execute(items_stmt)
    count_result = await session.execute(count_stmt)
    scans = list(items_result.scalars().all())
    total = int(count_result.scalar_one())
    return scans, total


async def _severity_distribution_by_scan(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, int]]:
    """``{scan_id: {bucket: component_count}}`` over the page's scans (worst CVE per cv).

    One grouped aggregation: collapse each (scan, component_version) to its worst
    CVE rank (MAX), then count how many components land in each severity bucket
    per scan. Mirrors ``dashboard_service._severity_counts`` but keeps ``scan_id``
    in the GROUP BY so each snapshot gets its own counts. ALL buckets (including
    ``info`` / ``none``) are accumulated so the risk-score formula sees the same
    distribution the Overview tab computes; the response then surfaces only the
    four risk-bearing buckets.
    """
    if not scan_ids:
        return {}

    sev_rank = _severity_rank_case()
    per_cv = (
        select(
            VulnerabilityFinding.scan_id.label("scan_id"),
            VulnerabilityFinding.component_version_id.label("cv_id"),
            func.max(sev_rank).label("max_rank"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(VulnerabilityFinding.scan_id.in_(scan_ids))
        .group_by(
            VulnerabilityFinding.scan_id,
            VulnerabilityFinding.component_version_id,
        )
        .subquery()
    )
    stmt = select(
        per_cv.c.scan_id,
        per_cv.c.max_rank,
        func.count().label("n"),
    ).group_by(per_cv.c.scan_id, per_cv.c.max_rank)

    result = await session.execute(stmt)
    distributions: dict[uuid.UUID, dict[str, int]] = {}
    for row in result.all():
        bucket = _SEVERITY_FROM_RANK.get(int(row.max_rank), "none")
        dist = distributions.setdefault(row.scan_id, {})
        dist[bucket] = dist.get(bucket, 0) + int(row.n)
    return distributions


async def _license_distribution_by_scan(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, int]]:
    """``{scan_id: {category: component_count}}`` over the page's scans (worst category per cv)."""
    if not scan_ids:
        return {}

    lic_rank = _license_rank_case()
    per_cv = (
        select(
            LicenseFinding.scan_id.label("scan_id"),
            LicenseFinding.component_version_id.label("cv_id"),
            func.max(lic_rank).label("max_rank"),
        )
        .select_from(LicenseFinding)
        .join(LicenseModel, LicenseModel.id == LicenseFinding.license_id)
        .where(LicenseFinding.scan_id.in_(scan_ids))
        .group_by(
            LicenseFinding.scan_id,
            LicenseFinding.component_version_id,
        )
        .subquery()
    )
    stmt = select(
        per_cv.c.scan_id,
        per_cv.c.max_rank,
        func.count().label("n"),
    ).group_by(per_cv.c.scan_id, per_cv.c.max_rank)

    result = await session.execute(stmt)
    distributions: dict[uuid.UUID, dict[str, int]] = {}
    for row in result.all():
        category = _LICENSE_CATEGORY_FROM_RANK.get(int(row.max_rank), "unknown")
        dist = distributions.setdefault(row.scan_id, {})
        dist[category] = dist.get(category, 0) + int(row.n)
    return distributions


async def _component_count_by_scan(
    session: AsyncSession,
    *,
    scan_ids: list[uuid.UUID],
) -> dict[uuid.UUID, int]:
    """``{scan_id: distinct component_version count}`` over the page's scans."""
    if not scan_ids:
        return {}

    from models import ScanComponent

    stmt = (
        select(
            ScanComponent.scan_id.label("scan_id"),
            func.count(func.distinct(ScanComponent.component_version_id)).label("n"),
        )
        .where(ScanComponent.scan_id.in_(scan_ids))
        .group_by(ScanComponent.scan_id)
    )
    result = await session.execute(stmt)
    return {row.scan_id: int(row.n) for row in result.all()}


def _release_label(scan: Scan) -> str | None:
    """Read the optional ``metadata.release`` label off a scan, or None.

    The label is validated git-ref-safe at write time (schemas.scan); here we
    only defensively coerce: a non-str / blank value reads as None so the row's
    ``release`` is always ``str | null``.
    """
    metadata = scan.scan_metadata or {}
    raw = metadata.get("release")
    if isinstance(raw, str):
        label = raw.strip()
        return label or None
    return None


async def list_release_snapshots(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    """List a project's release snapshots (succeeded scans), newest-first.

    Returns ``(items, total)`` where each item is a plain dict shaped to
    :class:`schemas.release_snapshot.ReleaseSnapshot`. ``total`` is the count of
    succeeded scans before pagination.

    Authorization mirrors :func:`get_project_overview`: ``ProjectNotFound`` (404)
    for a missing project, ``ProjectForbidden`` (403) for a non-member (super_admin
    bypasses). A project with no succeeded scan returns ``([], 0)`` — an empty
    200, never a 404.
    """
    page = max(int(page), 1)
    size = max(min(int(size), 100), 1)

    project_result = await session.execute(
        select(Project).where(Project.id == project_id)
    )
    project = project_result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_releases",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}",
        ),
    )

    scans, total = await _paged_succeeded_scans(
        session, project_id=project_id, page=page, size=size
    )
    if not scans:
        return [], total

    scan_ids = [s.id for s in scans]

    severity_by_scan = await _severity_distribution_by_scan(session, scan_ids=scan_ids)
    license_by_scan = await _license_distribution_by_scan(session, scan_ids=scan_ids)
    component_count_by_scan = await _component_count_by_scan(session, scan_ids=scan_ids)

    items: list[dict[str, Any]] = []
    for scan in scans:
        sev_dist = severity_by_scan.get(scan.id, {})
        lic_dist = license_by_scan.get(scan.id, {})
        risk_score = _compute_risk_score(sev_dist, lic_dist)

        # Per-scan build-gate verdict, pinned to THIS snapshot. evaluate_gate
        # returns gate='pass' with scan_id=None only when it resolved no
        # succeeded scan — impossible here since we pass a known succeeded id, so
        # gate_status is always 'pass' / 'fail' for a real snapshot. We still map
        # the no-verdict shape to None to keep the contract honest.
        gate_result = await evaluate_gate(session, project_id, scan_id=scan.id)
        gate_status: str | None = gate_result.gate if gate_result.scan_id is not None else None

        items.append(
            {
                "scan_id": scan.id,
                "release": _release_label(scan),
                "created_at": scan.created_at,
                "risk_score": risk_score,
                "severity_summary": {
                    bucket: sev_dist.get(bucket, 0) for bucket in _SUMMARY_BUCKETS
                },
                "gate_status": gate_status,
                "component_count": component_count_by_scan.get(scan.id, 0),
            }
        )

    return items, total


__all__ = ["list_release_snapshots"]
