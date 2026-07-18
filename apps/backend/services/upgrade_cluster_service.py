"""
Upgrade-cluster read service — W9-#53 ("Group by upgrade").

The Vulnerabilities tab lists one row per (component × CVE) finding. This module
computes the complementary *action-first* view Snyk popularised: instead of "here
are your CVEs", it answers "here are the component upgrades that clear them, most
leverage first". Each cluster is one component_version whose OPEN findings are
resolved (all at once) by a single **minimum safe upgrade** — the semver maximum
of the component's per-finding ``fixed_version`` values.

Nothing here re-implements the clustering math. The per-component recommendation
(minimum-safe-upgrade version, reason, priority signals) is
:func:`services.upgrade_recommendation.recommend_for_component`, used verbatim —
the same function that backs the vulnerability drawer's "Upgrade" panel and the
build-gate PR comment. This service only groups the scan's open findings by
component_version and assembles the wire shape.

Open-finding predicate
----------------------
A finding counts toward a cluster iff it is still open work in the SAME sense the
build gate counts it: we exclude the dispositioned statuses in
:data:`services.policy_gate._CLOSED_FINDING_STATUSES`
(``not_affected`` / ``fixed`` / ``false_positive``). ``suppressed`` is NOT closed
— a suppressed critical is still work the team owes. Reusing the gate's own set
(rather than a second hand-rolled list) keeps the ``total_findings`` contract in
lock-step with the gate / drawer counts, per the shared-vocabulary rule.

Authorization + scan resolution mirror
:func:`services.vulnerability_service.list_project_vulnerabilities` exactly:
load the project (404 if absent), assert team membership (403), then resolve the
snapshot scan (an invalid pinned ``snapshot_scan_id`` → 404). A project with no
succeeded scan yields an empty result (200), never an error.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.security import CurrentUser
from models import (
    Component,
    ComponentVersion,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from services.policy_gate import _CLOSED_FINDING_STATUSES
from services.scan_resolution import resolve_snapshot_scan_id
from services.upgrade_recommendation import (
    _SEVERITY_RANK,
    FindingSignal,
    priority_rank,
    recommend_for_component,
)

log = structlog.get_logger("upgrade_cluster.service")


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpgradeClusterResult:
    """The assembled "Group by upgrade" view for one resolved scan.

    ``clusters`` is a list of plain dicts shaped to
    :class:`schemas.vulnerability_detail.UpgradeCluster`, already sorted
    most-actionable first. ``total_findings`` is the sum of every cluster's
    ``finding_count`` and — by contract — equals the number of OPEN findings in
    the resolved scan. ``scan_id`` is the resolved snapshot (``None`` when the
    project has no succeeded scan, in which case ``clusters`` is empty).
    """

    clusters: list[dict[str, Any]] = field(default_factory=list)
    total_findings: int = 0
    scan_id: uuid.UUID | None = None


def _decimal_to_float(value: Any) -> float | None:
    """Serialize a ``Numeric`` column (Decimal | None | float) to float | None.

    EPSS is stored as ``Numeric`` so asyncpg returns :class:`decimal.Decimal`;
    the wire declares ``float | None``. Mirrors the helper in
    ``services.vulnerability_service``.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def list_upgrade_clusters(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    snapshot_scan_id: uuid.UUID | None = None,
) -> UpgradeClusterResult:
    """Group the resolved scan's OPEN findings into per-component upgrade clusters.

    Authorization / resolution (identical to ``list_project_vulnerabilities``):
      - ProjectNotFound (404) if the project id doesn't exist.
      - ProjectForbidden (403) if the actor is not a team member.
      - ``snapshot_scan_id`` (feature #28) optionally pins a SPECIFIC succeeded
        scan; a cross-project / non-succeeded / nonexistent id raises
        :class:`services.scan_resolution.SnapshotScanNotFound` (→ 404 at router).
      - No succeeded scan → empty result (200).

    Returns an :class:`UpgradeClusterResult`; ``total_findings`` MUST equal the
    number of open findings in the resolved scan.
    """
    # Reuse PR #10's project loader helpers; imported lazily to avoid a circular
    # import (project_service pulls in this package's siblings).
    from models import Project
    from services.project_service import ProjectForbidden, ProjectNotFound

    project_result = await session.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="project_upgrade_clusters",
        resource_id=str(project_id),
        deny=lambda: ProjectForbidden(
            f"actor is not a member of team {project.team_id}",
        ),
    )

    # Anchor on the resolved snapshot scan — the pinned id when given, else the
    # latest SUCCEEDED scan (never ``project.latest_scan_id``, the last attempt).
    # An invalid pinned id raises SnapshotScanNotFound → 404 at the router.
    scan_id = await resolve_snapshot_scan_id(session, project_id, snapshot_scan_id)
    if scan_id is None:
        return UpgradeClusterResult(clusters=[], total_findings=0, scan_id=None)

    # One row per OPEN finding, carrying its component / CVE columns. We do NOT
    # join ScanComponent here: a (scan, cv) can have several ScanComponent rows
    # (diamond deps / monorepos — see the uq on scan_id, cv_id, dependency_path),
    # so joining would fan the finding out and inflate finding_count. The direct
    # signal is resolved separately with a grouped aggregate below.
    finding_stmt = (
        select(
            VulnerabilityFinding.id.label("finding_id"),
            VulnerabilityFinding.component_version_id.label("component_version_id"),
            cast(VulnerabilityFinding.status, String).label("status"),
            VulnerabilityFinding.fixed_version.label("fixed_version"),
            ComponentVersion.version.label("current_version"),
            Component.name.label("component_name"),
            Component.purl.label("component_purl"),
            Vulnerability.external_id.label("cve_id"),
            cast(Vulnerability.severity, String).label("severity"),
            Vulnerability.kev.label("kev"),
            Vulnerability.epss_score.label("epss_score"),
        )
        .select_from(VulnerabilityFinding)
        .join(
            ComponentVersion,
            ComponentVersion.id == VulnerabilityFinding.component_version_id,
        )
        .join(Component, Component.id == ComponentVersion.component_id)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(VulnerabilityFinding.scan_id == scan_id)
        .where(cast(VulnerabilityFinding.status, String).notin_(_CLOSED_FINDING_STATUSES))
    )
    finding_rows = (await session.execute(finding_stmt)).all()

    # Per-cv direct signal for THIS scan: direct iff any dependency path is a
    # direct dep (``direct`` flag) OR the shortest path depth == 1. bool_or /
    # min(depth) collapse the multiple ScanComponent rows a diamond dep produces.
    direct_stmt = (
        select(
            ScanComponent.component_version_id.label("component_version_id"),
            func.bool_or(ScanComponent.direct).label("any_direct"),
            func.min(ScanComponent.depth).label("min_depth"),
        )
        .where(ScanComponent.scan_id == scan_id)
        .group_by(ScanComponent.component_version_id)
    )
    direct_map: dict[uuid.UUID, bool] = {}
    for row in (await session.execute(direct_stmt)).all():
        min_depth = row.min_depth
        direct_map[row.component_version_id] = bool(row.any_direct) or (
            min_depth is not None and int(min_depth) == 1
        )

    # Group finding rows by component_version.
    groups: dict[uuid.UUID, list[Any]] = {}
    for row in finding_rows:
        groups.setdefault(row.component_version_id, []).append(row)

    clusters: list[dict[str, Any]] = []
    for cv_id, rows in groups.items():
        is_direct = direct_map.get(cv_id, False)
        signals = [
            FindingSignal(
                fixed_version=r.fixed_version,
                severity=str(r.severity),
                epss_score=_decimal_to_float(r.epss_score),
            )
            for r in rows
        ]
        rec = recommend_for_component(signals, direct=is_direct)

        findings = [
            {
                "finding_id": r.finding_id,
                "cve_id": r.cve_id,
                "severity": str(r.severity),
                "status": str(r.status),
                "epss_score": _decimal_to_float(r.epss_score),
                "kev": bool(r.kev),
                "fixed_version": r.fixed_version,
            }
            for r in rows
        ]
        # Within a cluster: worst CVE first (severity rank desc), tie-break by
        # cve_id asc for a deterministic order.
        findings.sort(
            key=lambda f: (-_SEVERITY_RANK.get(f["severity"], 0), f["cve_id"]),
        )

        # A representative row for the component's identity columns (they are
        # constant within a group — same cv → same component / version / purl).
        head = rows[0]
        clusters.append(
            {
                "component_version_id": cv_id,
                "component_name": head.component_name,
                "component_purl": head.component_purl,
                "current_version": head.current_version,
                "recommended_version": rec.recommended_version,
                "reason": rec.reason,
                "direct": rec.direct,
                "max_severity": rec.max_severity,
                "max_epss": rec.max_epss,
                "finding_count": rec.finding_count,
                "findings": findings,
                # Kept out-of-band for the deterministic sort below; not on the wire.
                "_priority": priority_rank(rec),
            }
        )

    # Most-actionable first: priority_rank desc, tie-break by component_name asc.
    clusters.sort(key=lambda c: c["component_name"])
    clusters.sort(key=lambda c: c["_priority"], reverse=True)
    for c in clusters:
        del c["_priority"]

    total_findings = sum(c["finding_count"] for c in clusters)

    log.info(
        "upgrade_clusters.listed",
        project_id=str(project_id),
        scan_id=str(scan_id),
        cluster_count=len(clusters),
        total_findings=total_findings,
    )

    return UpgradeClusterResult(
        clusters=clusters,
        total_findings=total_findings,
        scan_id=scan_id,
    )


__all__ = ["UpgradeClusterResult", "list_upgrade_clusters"]
