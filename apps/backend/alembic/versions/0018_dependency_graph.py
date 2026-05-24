"""dependency graph — scan_components.depth + component_dependency_edges (v2.2 2.2-a2)

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-24

Phase: v2.2 (Track A — a2 "dependency graph collection (depth)")
PR: feat/v2.2-dep-graph
Kind: schema (additive — expand step; no data migration)
Forward-only: yes

What:
  - Add one nullable column to the existing ``scan_components`` table::
      depth  SMALLINT NULL
    plus a composite index ``ix_scan_components_scan_depth (scan_id, depth)``.
  - Create a new ``component_dependency_edges`` table holding the cdxgen
    dependency *graph* (one row per resolved ``parent dependsOn child`` edge),
    scoped to a scan, both endpoints FK → ``component_versions``.

Why:
  - cdxgen's CycloneDX SBOM carries a top-level ``dependencies`` array
    (``ref`` → ``dependsOn[]``) describing the resolved dependency graph. Until
    now only the flat ``components`` list was persisted — the graph (and with it
    each component's *depth* from a root) was discarded.
  - 2.2-a3 (upgrade recommendation) prioritises remediations by direct/transitive
    + depth: "fix shallow, directly-depended components first". That needs (a) a
    per-component depth and (b) the parent/child edges to answer "who depends on
    X?" when proposing a bump. ``depth`` lands on ``scan_components``; the raw
    adjacency lands in ``component_dependency_edges``.

depth semantics:
  - Shortest-path distance from a graph root (the scanned project / a top-level
    direct dependency). Direct deps = 1, transitive = 2+. NULL = "graph not
    available" (older scans, ecosystems that produced only a flat list) — a
    legitimate permanent value, distinct from any computed depth.
  - ``integrations.dependency_graph`` computes it with a cycle-safe BFS and
    clamps it at ``MAX_DEPTH`` (64), so SMALLINT (max 32767) never overflows
    even for a hostile multi-thousand-deep / cyclic graph.

component_dependency_edges:
  - ``(scan_id, parent_component_version_id, child_component_version_id)`` is
    UNIQUE so a re-run that re-ingests the same graph is idempotent (it collapses
    onto the same rows). Only edges where BOTH endpoints resolved to a persisted
    ``component_versions`` row are stored — dangling refs and the scanned
    project's own metadata component are dropped at ingest, never invented here.
  - ``ON DELETE CASCADE`` on ``scan_id`` so a scan delete / re-run reset reclaims
    every edge with the scan, matching ``scan_components``' lifecycle.
  - Forward (parent) and reverse (child) composite indexes serve 2.2-a3's
    "children of X" / "dependents of Y" traversals.

Notes:
  - **Expand step only** (CLAUDE.md §6 expand → migrate-data → contract),
    matching 0015/0016/0017. The ``depth`` column is NULLABLE with no server
    default: existing scan_components rows start NULL and are backfilled by the
    next scan (or a re-scan). No contract step planned.
  - Pure additive DDL (ADD COLUMN NULL is metadata-only on PG 11+; CREATE TABLE
    is new). No raw SQL → no asyncpg ``::`` / TIMESTAMPTZ bind concerns.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- scan_components.depth (expand) ---
    op.add_column(
        "scan_components",
        sa.Column("depth", sa.SmallInteger(), nullable=True),
    )
    op.create_index(
        "ix_scan_components_scan_depth",
        "scan_components",
        ["scan_id", "depth"],
    )

    # --- component_dependency_edges (new table) ---
    op.create_table(
        "component_dependency_edges",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "scan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_component_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("component_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "child_component_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("component_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "scan_id",
            "parent_component_version_id",
            "child_component_version_id",
            name="uq_dep_edges_scan_parent_child",
        ),
    )
    op.create_index(
        "ix_dep_edges_scan_id",
        "component_dependency_edges",
        ["scan_id"],
    )
    op.create_index(
        "ix_dep_edges_scan_parent",
        "component_dependency_edges",
        ["scan_id", "parent_component_version_id"],
    )
    op.create_index(
        "ix_dep_edges_scan_child",
        "component_dependency_edges",
        ["scan_id", "child_component_version_id"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
