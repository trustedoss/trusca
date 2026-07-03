"""
Dependency-graph schemas — BomLens parity Phase H-1.

``GET /v1/projects/{project_id}/dependency-graph`` returns the resolved
dependency graph of ONE succeeded scan (a release snapshot): every
``component_version`` the scan observed as a node, plus the ``parent dependsOn
child`` edges cdxgen resolved (``component_dependency_edges``). The frontend
renders this as an interactive node/edge diagram (with a tree fallback when the
graph is too large — see ``truncated`` below).

Node identity is the ``component_version`` uuid (as a string), so an edge's
``source`` / ``target`` reference node ids directly and the frontend can wire
the graph without a second lookup. Isolated nodes (a component_version present
in the scan but touched by no edge) are still emitted — the scan saw them.

Defensive node cap
------------------
A pathological scan could carry tens of thousands of components; shipping the
whole adjacency to the browser would freeze the render. When the scan's node
count exceeds ``node_cap`` (``DEPENDENCY_GRAPH_MAX_NODES``, default 5000) the
response sets ``truncated=true`` and returns EMPTY ``nodes`` / ``edges`` lists
so the frontend renders its tree fallback / guidance instead. ``node_count`` and
``edge_count`` are ALWAYS exact (they come from ``COUNT`` queries, never from the
enumerated lists), so the UI can tell the user exactly how big the graph is.

All field names are snake_case (CLAUDE.md §1.2 OpenAPI convention). The schemas
are registered in OpenAPI via the endpoint's ``response_model``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The severity buckets a node's worst finding can land in. ``none`` when the
# node carries no vulnerability finding at all. ``unknown`` is in the contract
# for completeness, but the aggregation normalises an unknown-severity CVE to
# ``info`` (same as the components list / overview), so it is not emitted in
# practice.
MaxSeverity = Literal["critical", "high", "medium", "low", "info", "none", "unknown"]


class GraphNode(BaseModel):
    """One ``component_version`` observed in the scan — a node of the graph."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(
        description="The component_version uuid (as a string). Edges reference this id."
    )
    name: str = Field(description="The component (package) name.")
    namespace: str | None = Field(
        default=None,
        description="The package namespace/group (e.g. Maven group id), or null.",
    )
    version: str = Field(description="The component version string.")
    purl: str = Field(description="The full package URL (purl-with-version).")
    direct: bool = Field(
        description=(
            "True when the project reaches this component_version directly (graph "
            "depth 1 on at least one path). False for transitive-only nodes and "
            "nodes whose scan carried no depth information."
        )
    )
    depth: int | None = Field(
        default=None,
        description=(
            "Shallowest graph depth at which the project reaches this node "
            "(1 = direct). Null when the scan carried no dependency graph."
        ),
    )
    vulnerability_count: int = Field(
        default=0,
        ge=0,
        description="Distinct vulnerabilities affecting this node in this scan.",
    )
    max_severity: MaxSeverity = Field(
        default="none",
        description=(
            "The worst vulnerability severity affecting this node "
            "(critical/high/medium/low/info), or 'none' when it has no findings."
        ),
    )


class GraphEdge(BaseModel):
    """One ``parent dependsOn child`` edge between two nodes of the graph."""

    model_config = ConfigDict(from_attributes=True)

    source: str = Field(
        description="Parent node id (the component_version that depends on the child)."
    )
    target: str = Field(
        description="Child node id (the component_version depended upon)."
    )


class ProjectDependencyGraph(BaseModel):
    """The resolved dependency graph of one succeeded-scan snapshot."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "scan_id": "3c15c82f-c409-4f5f-b7d9-92bca8cc1f7f",
                "node_count": 3,
                "edge_count": 2,
                "node_cap": 5000,
                "truncated": False,
                "nodes": [
                    {
                        "id": "0a4d1f2e-1111-4a2b-9c3d-000000000001",
                        "name": "app",
                        "namespace": None,
                        "version": "1.0.0",
                        "purl": "pkg:npm/app@1.0.0",
                        "direct": True,
                        "depth": 1,
                        "vulnerability_count": 0,
                        "max_severity": "none",
                    },
                    {
                        "id": "0a4d1f2e-1111-4a2b-9c3d-000000000002",
                        "name": "lodash",
                        "namespace": None,
                        "version": "4.17.20",
                        "purl": "pkg:npm/lodash@4.17.20",
                        "direct": False,
                        "depth": 2,
                        "vulnerability_count": 1,
                        "max_severity": "high",
                    },
                    {
                        "id": "0a4d1f2e-1111-4a2b-9c3d-000000000003",
                        "name": "log4j-core",
                        "namespace": "org.apache.logging.log4j",
                        "version": "2.14.1",
                        "purl": "pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1",
                        "direct": False,
                        "depth": 2,
                        "vulnerability_count": 1,
                        "max_severity": "critical",
                    },
                ],
                "edges": [
                    {
                        "source": "0a4d1f2e-1111-4a2b-9c3d-000000000001",
                        "target": "0a4d1f2e-1111-4a2b-9c3d-000000000002",
                    },
                    {
                        "source": "0a4d1f2e-1111-4a2b-9c3d-000000000001",
                        "target": "0a4d1f2e-1111-4a2b-9c3d-000000000003",
                    },
                ],
            }
        },
    )

    scan_id: uuid.UUID = Field(
        description="The succeeded scan whose resolved dependency graph this is."
    )
    node_count: int = Field(
        ge=0,
        description=(
            "Total nodes (distinct component_versions) in this scan's graph. "
            "ALWAYS exact, even when truncated."
        ),
    )
    edge_count: int = Field(
        ge=0,
        description=(
            "Total edges (parent→child) in this scan's graph. ALWAYS exact, even "
            "when truncated."
        ),
    )
    node_cap: int = Field(
        ge=1,
        description="The applied node ceiling (DEPENDENCY_GRAPH_MAX_NODES).",
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True when node_count exceeds node_cap. The graph is then too large to "
            "ship whole: nodes and edges are EMPTY and the frontend renders its "
            "tree fallback. node_count / edge_count stay exact so the UI can size "
            "the graph. False otherwise (nodes/edges are complete)."
        ),
    )
    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="The graph nodes. Empty when truncated.",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="The graph edges. Empty when truncated.",
    )


__all__ = [
    "GraphEdge",
    "GraphNode",
    "MaxSeverity",
    "ProjectDependencyGraph",
]
