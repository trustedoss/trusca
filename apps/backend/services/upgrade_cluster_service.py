# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Upgrade-cluster read service — W9-#53 ("Group by upgrade").

The Vulnerabilities tab lists one row per (component × CVE) finding. This module
computes the complementary *action-first* view: instead of "here are your
CVEs", it answers "here are the component upgrades that clear them, most
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
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import String, case, cast, func, select
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

#: Clusters returned when the caller does not ask for a number, matching the
#: other list endpoints.
DEFAULT_CLUSTER_LIMIT = 50

#: The most this will return, and therefore the most components whose findings
#: it reads. Other ``limit`` endpoints cap at 500, and that number does not
#: transfer: there the cap bounds the response, and the rows come from a query
#: the database has already narrowed. Here it bounds the read, because the
#: recommendation for one component needs every open finding on it. 200 is the
#: cap this repository uses where the number governs work rather than payload.
MAX_CLUSTER_LIMIT = 200


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpgradeClusterResult:
    """The assembled "Group by upgrade" view for one resolved scan.

    ``clusters`` is a list of plain dicts shaped to
    :class:`schemas.vulnerability_detail.UpgradeCluster`, already sorted
    most-actionable first, and at most ``limit`` long.

    ``total_findings`` is the number of OPEN findings in the resolved scan and
    ``total_clusters`` the number of components carrying them. Both come from
    aggregates rather than from summing the list, so they stay true when the
    list is a page. Before the list was bounded they were the same number
    either way; now they differ whenever ``truncated`` is set, and a caller
    showing "N findings across M components" wants the totals, not the page.

    ``scan_id`` is the resolved snapshot (``None`` when the project has no
    succeeded scan, in which case ``clusters`` is empty).
    """

    clusters: list[dict[str, Any]] = field(default_factory=list)
    total_findings: int = 0
    total_clusters: int = 0
    truncated: bool = False
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


def _severity_rank_sql() -> Any:
    """``_SEVERITY_RANK`` as a CASE expression, derived from the map itself.

    The ranking lives in Python because that is where the recommendation is
    computed. Ordering candidates before reading their findings needs the same
    ranking in SQL, and writing the numbers out a second time is how one
    vocabulary becomes two that drift. ``else_`` matches ``dict.get(sev, 0)``:
    without it an unmapped severity yields NULL, which sorts first on DESC and
    would lead the list with the least-known component.
    """
    return case(
        dict(_SEVERITY_RANK),
        value=cast(Vulnerability.severity, String),
        else_=0,
    )


def _candidate_ranking_stmt(scan_id: uuid.UUID) -> Any:
    """One row per component version in the scan, with the sort key SQL can do.

    ``priority_rank`` is (direct_term, severity_rank, max_epss) descending. Two
    of the three are aggregates over a component's open findings and belong
    here. The first is ``direct AND actionable``, and "actionable" needs the
    version parser, so this produces the two SQL can and the caller supplies the
    rest once it has a recommendation.

    ``component_name`` rides along because it is the tie-break; fetching it
    later would mean a second query keyed on the ids this one returns.
    """
    return (
        select(
            VulnerabilityFinding.component_version_id.label("component_version_id"),
            Component.name.label("component_name"),
            func.max(_severity_rank_sql()).label("severity_rank"),
            # coalesce, though nothing here sorts in SQL today: the tail is
            # ordered in Python, which already reads this as ``or 0``. Removing
            # it changes no result, and it stays because the moment an
            # ``ORDER BY`` lands on this column the NULL would sort FIRST on
            # DESC and put the components with no EPSS at the head. That is a
            # silent reversal, and one word here costs nothing.
            func.coalesce(func.max(Vulnerability.epss_score), 0).label("max_epss"),
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
        .group_by(VulnerabilityFinding.component_version_id, Component.name)
    )


def _finding_rows_stmt(
    scan_id: uuid.UUID, component_version_ids: Sequence[uuid.UUID]
) -> Any:
    """One row per OPEN finding on the given components.

    ScanComponent is deliberately not joined: a (scan, cv) can have several
    ScanComponent rows (diamond deps / monorepos, see the uq on scan_id, cv_id,
    dependency_path), so joining would fan the finding out and inflate
    finding_count. The direct signal is a separate grouped aggregate.

    The component filter is what bounds the read. Passing every component makes
    this the query this service used to run unconditionally.
    """
    return (
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
        .where(VulnerabilityFinding.component_version_id.in_(component_version_ids))
    )


def _build_clusters(
    groups: dict[uuid.UUID, list[Any]],
    direct_map: dict[uuid.UUID, bool],
) -> list[dict[str, Any]]:
    """Turn grouped finding rows into clusters.

    Lifted out unchanged so both read stages build clusters the same way.
    The second stage exists because the first one may not fill the page,
    and a second copy of this loop is exactly where the two would drift.
    """
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
    return clusters


async def list_upgrade_clusters(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    snapshot_scan_id: uuid.UUID | None = None,
    limit: int = DEFAULT_CLUSTER_LIMIT,
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

    # Anchor on the resolved snapshot scan: the pinned id when given, else the
    # latest SUCCEEDED scan (never ``project.latest_scan_id``, the last attempt).
    # An invalid pinned id raises SnapshotScanNotFound → 404 at the router.
    scan_id = await resolve_snapshot_scan_id(session, project_id, snapshot_scan_id)
    if scan_id is None:
        return UpgradeClusterResult(clusters=[], total_findings=0, scan_id=None)

    # Which components this scan has, ordered by the part of the sort key SQL
    # can compute. Reading this first is what bounds the findings read below:
    # without it there is no way to know which components matter without
    # loading every open finding, which is what this used to do.
    candidates = (await session.execute(_candidate_ranking_stmt(scan_id))).all()
    if not candidates:
        return UpgradeClusterResult(clusters=[], total_findings=0, scan_id=scan_id)

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

    # The read is split in two, and what the split buys is what the second read
    # does not do.
    #
    # ``priority_rank`` is (direct_term, severity_rank, max_epss) compared
    # descending, and ``direct_term`` is 1 only when a component is BOTH direct
    # and actionable. Lexicographic order therefore places every such component
    # ahead of every other one, whatever their severities. So the head of the
    # list is drawn entirely from the direct components, and a component that is
    # not direct cannot enter it however its recommendation turns out.
    #
    # Read the direct components' findings, build their clusters, and if that
    # fills the page nothing else is read.
    direct_ids = [
        c.component_version_id
        for c in candidates
        if direct_map.get(c.component_version_id, False)
    ]
    groups: dict[uuid.UUID, list[Any]] = {}
    if direct_ids:
        for row in (
            await session.execute(_finding_rows_stmt(scan_id, direct_ids))
        ).all():
            groups.setdefault(row.component_version_id, []).append(row)

    clusters = _build_clusters(groups, direct_map)

    total_clusters = len(candidates)

    # A direct component whose recommendation is not actionable drops out of the
    # head of the list, so the head can be smaller than the direct set. That is
    # not rare: a component keeps its place only if every open finding on it
    # carries a parseable fixed version, and real advisories often carry none.
    # Those components are not discarded; they join the tail candidates below,
    # and their findings are already in hand.
    leading = sum(1 for c in clusters if c["_priority"][0] == 1)

    if leading < limit:
        # The page is not full, so the tail matters. Its order is
        # (0, severity_rank, max_epss), which the candidate query computed, so
        # the shortfall is taken off the top of that ordering without reading
        # anything to find it.
        already = set(groups)
        tail = sorted(
            (c for c in candidates if c.component_version_id not in already),
            key=lambda c: (c.severity_rank, float(c.max_epss or 0), ),
            reverse=True,
        )
        to_read = [c.component_version_id for c in tail[: limit - leading]]
        if to_read:
            extra: dict[uuid.UUID, list[Any]] = {}
            for row in (
                await session.execute(_finding_rows_stmt(scan_id, to_read))
            ).all():
                extra.setdefault(row.component_version_id, []).append(row)
            clusters.extend(_build_clusters(extra, direct_map))

    # Most-actionable first: priority_rank desc, tie-break by component_name asc.
    clusters.sort(key=lambda c: c["component_name"])
    clusters.sort(key=lambda c: c["_priority"], reverse=True)
    for c in clusters:
        del c["_priority"]

    # Against the number of components that exist, not the number read. The
    # second stage reads exactly the shortfall, so ``len(clusters)`` reaches
    # ``limit`` and never exceeds it: comparing against that would report "not
    # truncated" on every bounded read, which is the opposite of the truth.
    truncated = total_clusters > limit
    clusters = clusters[:limit]

    # From an aggregate, not from what was read. The list is at most ``limit``
    # entries, and this stays the true size of the thing that list is a page of.
    total_findings = int(
        (
            await session.execute(
                select(func.count())
                .select_from(VulnerabilityFinding)
                .where(VulnerabilityFinding.scan_id == scan_id)
                .where(
                    cast(VulnerabilityFinding.status, String).notin_(
                        _CLOSED_FINDING_STATUSES
                    )
                )
            )
        ).scalar_one()
    )

    log.info(
        "upgrade_clusters.listed",
        project_id=str(project_id),
        scan_id=str(scan_id),
        cluster_count=len(clusters),
        total_clusters=total_clusters,
        total_findings=total_findings,
        truncated=truncated,
        components_read=len(groups),
    )

    return UpgradeClusterResult(
        clusters=clusters,
        total_findings=total_findings,
        total_clusters=total_clusters,
        truncated=truncated,
        scan_id=scan_id,
    )


__all__ = ["UpgradeClusterResult", "list_upgrade_clusters"]
