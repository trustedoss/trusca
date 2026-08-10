# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""record the dependency manifests a scan had in front of it

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-10

Phase: scan provenance (gap #31)
Kind: schema (additive — one nullable column; no data migration)
Forward-only: yes

What:
  ``scans.input_manifests`` — the manifests and lockfiles found in the scanned
  tree, as ``{"files": [{"path", "size", "sha256"}], "count", "truncated"}``.
  Nullable JSONB, no default.

Why a column and not a file:
  The obvious home was a ``ScanArtifact`` row pointing at a file on disk, like
  the Trivy report and the cdxgen SBOM. Those are transient — the per-scan
  workspace is deleted when the scan ends — and the one durable artifact tree
  (``scan-sources/``) is retained latest-succeeded-per-project by
  ``tasks.scan_source_cleaner``. This inventory has to outlive both: the
  question it answers ("did the scan see the file that declares this?") is
  asked about scans that finished long ago, which is precisely when the tarball
  is gone. A new durable path would need its own reclaim sweeper, whereas a
  column is reclaimed by the delete that already cascades a scan's children.

Why NULL is meaningful:
  NULL means "not recorded", which every scan before this migration is, and
  which container and SBOM-ingest scans stay — neither has a source tree. An
  empty object would claim the scan looked and found nothing, and the two are
  not the same answer. Readers must distinguish them.

Sizing:
  Bounded at collection: 2000 entries, 12 directory levels, vendored trees
  skipped (``services/scan_inputs.py``). A monorepo with hundreds of modules
  lands in the low hundreds of KB; the ceiling exists so that a tree the walk
  should not have entered cannot put an unbounded value in a row.

Index:
  None. The column is read for one scan at a time, on the scan-detail path,
  never filtered or aggregated across scans.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("input_manifests", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("downgrade is not supported")
