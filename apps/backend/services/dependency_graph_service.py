"""
Dependency-graph service — BomLens parity Phase H-1.

Powers ``GET /v1/projects/{project_id}/dependency-graph``: given ONE succeeded
scan (a release snapshot), serialize its resolved dependency graph — every
``component_version`` the scan observed as a node, plus the ``parent dependsOn
child`` edges cdxgen persisted into ``component_dependency_edges``.

Node set / edge set
-------------------
* Nodes are EVERY distinct ``component_version`` in the scan's ``scan_components``
  (isolated nodes — present in the scan but touched by no edge — are included).
  Per-node ``direct`` / ``depth`` collapse the (possibly several) dependency
  paths the same component_version sits on: ``depth`` = the SHALLOWEST path
  (``MIN``), ``direct`` = OR of the per-path direct flags — the SAME "strongest
  claim" collapse the components list uses.
* ``vulnerability_count`` / ``max_severity`` per node aggregate the scan's
  ``vulnerability_findings`` for that component_version. We aggregate findings in
  a SEPARATE query from the node scan_components query (rather than one big join)
  so a diamond dependency — the same cv on several scan_component paths — cannot
  fan out a cartesian product and over-count its vulnerabilities.
* Edges are every ``component_dependency_edges`` row of the scan; ``source`` =
  ``parent_component_version_id``, ``target`` = ``child_component_version_id``.

Defensive node cap
------------------
``node_count`` and ``edge_count`` are computed with ``COUNT`` queries FIRST. When
``node_count`` exceeds :func:`core.config.dependency_graph_max_nodes` the graph is
too large to render in the browser: we return ``truncated=true`` with EMPTY
nodes/edges (the frontend falls back to a tree view) and DO NOT run the two heavy
enumeration queries. The counts are always exact.

Authorization
-------------
Mirrors ``services.project_diff_service`` / the other per-project detail readers:
load the project, then ``assert_team_access``. The deny path here raises
``ProjectNotFound`` (404) rather than ``ProjectForbidden`` (403) so a non-member
learns nothing about whether the project exists — existence-hide, matching the
graph endpoint's contract (non-member and missing-project are indistinguishable
404s). super_admin bypasses membership. The optional ``scan_id`` snapshot anchor
is resolved through :func:`services.scan_resolution.resolve_snapshot_scan_id`
(belongs to THIS project AND ``status='succeeded'``); an invalid / cross-project /
non-succeeded id — and the "no succeeded scan at all" case — raise
:class:`SnapshotScanNotFound` → existence-hide 404 at the router.

CLAUDE.md compliance: pure async-SQLAlchemy reads against PostgreSQL, no schema
change (the ``component_dependency_edges`` table + its indexes already exist).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.authz import assert_team_access
from core.config import dependency_graph_max_nodes
from core.security import CurrentUser
from models import (
    Component,
    ComponentDependencyEdge,
    ComponentVersion,
    Project,
    ScanComponent,
    Vulnerability,
    VulnerabilityFinding,
)
from services.project_detail_service import (
    _SEVERITY_FROM_RANK,
    _severity_rank_case,
)
from services.project_service import ProjectNotFound
from services.scan_resolution import (
    SnapshotScanNotFound,
    resolve_snapshot_scan_id,
)

log = structlog.get_logger("dependency_graph.service")


async def get_dependency_graph(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    scan_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Serialize the dependency graph of a project's resolved scan snapshot.

    Returns a plain dict shaped to :class:`schemas.dependency_graph.ProjectDependencyGraph`.

    Raises :class:`ProjectNotFound` (404) when the project is missing OR the
    actor is not a member of its owning team (existence-hide; super_admin
    bypasses). Raises :class:`services.scan_resolution.SnapshotScanNotFound`
    (→ 404 at the router) when ``scan_id`` is an invalid / cross-project /
    non-succeeded pin, or when no succeeded scan exists to read.
    """
    project = await session.scalar(select(Project).where(Project.id == project_id))
    if project is None:
        raise ProjectNotFound(f"project {project_id} not found")

    assert_team_access(
        actor,
        project.team_id,
        log=log,
        resource="dependency_graph",
        resource_id=str(project_id),
        # Existence-hide: a non-member gets the SAME 404 as a missing project so a
        # cross-team probe learns nothing about the project's existence.
        deny=lambda: ProjectNotFound(f"project {project_id} not found"),
    )

    # Anchor on the resolved snapshot: the pinned scan_id when given (validated
    # as a succeeded scan of THIS project), else the latest succeeded scan. A
    # None result means the project has never had a succeeded scan — there is no
    # immutable graph to read, so we existence-hide it as a 404 (the response
    # contract requires a concrete scan_id).
    resolved_scan_id = await resolve_snapshot_scan_id(session, project_id, scan_id)
    if resolved_scan_id is None:
        raise SnapshotScanNotFound(
            f"project {project_id} has no succeeded scan to read a graph from"
        )

    node_cap = dependency_graph_max_nodes()

    # Exact counts FIRST (never derived from the enumerated lists) so a truncated
    # response can still report the true graph size without the heavy fetches.
    node_count = int(
        await session.scalar(
            select(func.count(func.distinct(ScanComponent.component_version_id))).where(
                ScanComponent.scan_id == resolved_scan_id
            )
        )
        or 0
    )
    edge_count = int(
        await session.scalar(
            select(func.count())
            .select_from(ComponentDependencyEdge)
            .where(ComponentDependencyEdge.scan_id == resolved_scan_id)
        )
        or 0
    )

    truncated = node_count > node_cap

    result: dict[str, Any] = {
        "scan_id": resolved_scan_id,
        "node_count": node_count,
        "edge_count": edge_count,
        "node_cap": node_cap,
        "truncated": truncated,
        "nodes": [],
        "edges": [],
    }

    # Over the cap: skip the two enumeration queries entirely. The frontend
    # renders its tree fallback from the exact counts alone.
    if truncated:
        return result

    result["nodes"] = await _load_nodes(session, scan_id=resolved_scan_id)
    result["edges"] = await _load_edges(session, scan_id=resolved_scan_id)
    return result


async def _load_nodes(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Every component_version node of the scan (isolated nodes included).

    Two queries, merged in Python:
      1. the node set from ``scan_components`` (grouped by cv → shallowest depth,
         OR'd direct flag) joined to component_version + component for the
         display fields;
      2. per-cv vulnerability aggregation from ``vulnerability_findings`` — kept
         SEPARATE so a diamond dependency (same cv on several paths) cannot
         cartesian-inflate the vulnerability count.
    """
    # (2) Vulnerability aggregation — distinct vulns + worst severity per cv.
    vuln_stmt = (
        select(
            VulnerabilityFinding.component_version_id.label("cv_id"),
            func.count(func.distinct(VulnerabilityFinding.vulnerability_id)).label(
                "vuln_count"
            ),
            func.coalesce(func.max(_severity_rank_case()), 0).label("max_sev_rank"),
        )
        .select_from(VulnerabilityFinding)
        .join(Vulnerability, Vulnerability.id == VulnerabilityFinding.vulnerability_id)
        .where(VulnerabilityFinding.scan_id == scan_id)
        .group_by(VulnerabilityFinding.component_version_id)
    )
    vuln_by_cv: dict[uuid.UUID, tuple[int, int]] = {}
    for row in (await session.execute(vuln_stmt)).all():
        vuln_by_cv[row.cv_id] = (int(row.vuln_count), int(row.max_sev_rank))

    # (1) Node set — one row per distinct cv in the scan.
    node_stmt = (
        select(
            ComponentVersion.id.label("cv_id"),
            Component.name.label("name"),
            Component.namespace.label("namespace"),
            ComponentVersion.version.label("version"),
            ComponentVersion.purl_with_version.label("purl"),
            func.min(ScanComponent.depth).label("min_depth"),
            func.bool_or(ScanComponent.direct).label("is_direct"),
        )
        .select_from(ScanComponent)
        .join(ComponentVersion, ComponentVersion.id == ScanComponent.component_version_id)
        .join(Component, Component.id == ComponentVersion.component_id)
        .where(ScanComponent.scan_id == scan_id)
        .group_by(
            ComponentVersion.id,
            Component.name,
            Component.namespace,
            ComponentVersion.version,
            ComponentVersion.purl_with_version,
        )
        # Deterministic order so the payload is stable across runs.
        .order_by(Component.name.asc(), ComponentVersion.version.asc(), ComponentVersion.id.asc())
    )

    nodes: list[dict[str, Any]] = []
    for row in (await session.execute(node_stmt)).all():
        vuln_count, sev_rank = vuln_by_cv.get(row.cv_id, (0, 0))
        nodes.append(
            {
                "id": str(row.cv_id),
                "name": row.name,
                "namespace": row.namespace,
                "version": row.version,
                "purl": row.purl,
                "direct": bool(row.is_direct),
                "depth": int(row.min_depth) if row.min_depth is not None else None,
                "vulnerability_count": vuln_count,
                "max_severity": _SEVERITY_FROM_RANK.get(sev_rank, "none"),
            }
        )
    return nodes


async def _load_edges(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
) -> list[dict[str, str]]:
    """Every ``parent dependsOn child`` edge of the scan (ix_dep_edges_scan_id)."""
    edge_stmt = (
        select(
            ComponentDependencyEdge.parent_component_version_id.label("source"),
            ComponentDependencyEdge.child_component_version_id.label("target"),
        )
        .where(ComponentDependencyEdge.scan_id == scan_id)
        # Deterministic order for a stable payload.
        .order_by(
            ComponentDependencyEdge.parent_component_version_id.asc(),
            ComponentDependencyEdge.child_component_version_id.asc(),
        )
    )
    return [
        {"source": str(row.source), "target": str(row.target)}
        for row in (await session.execute(edge_stmt)).all()
    ]


__all__ = ["get_dependency_graph"]
