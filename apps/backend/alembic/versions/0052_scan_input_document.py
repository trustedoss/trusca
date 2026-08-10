# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""record what an ingested scan was handed

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-10

Phase: scan provenance (gap #31, step 2)
Kind: schema (additive — one nullable column; no data migration)
Forward-only: yes

What:
  ``scans.input_document`` — what an uploaded SBOM said about itself: format,
  spec version, the tools and authors it names, the timestamp it carries, the
  subject it describes and how many components it declared. Nullable JSONB.

Why a second column rather than reusing 0051's:
  ``input_manifests`` answers "which files could this scan read", which only a
  source scan has. An ingest scan has no tree — it has one document somebody
  else produced, and the equivalent question is "what was I given". Putting two
  different shapes behind one name would make every reader check which kind of
  scan it was looking at before it could interpret the value. Two columns, each
  NULL on the paths where its question does not apply.

Why the claims and not our own measurements:
  Everything stored is what the document states about itself. A generator can
  write a timestamp that is wrong and name a supplier that is nobody; the claim
  is still the point, because the claim is what a reader compares a scan's
  results against.

Why NULL is meaningful:
  NULL means not recorded. Every source and container scan stays NULL, and so
  does an ingest of a document this cannot parse — SPDX Tag-Value is accepted
  for ingest but not summarised, and a summary assembled from an unparsed
  document would state a spec version nobody read.

Sizing:
  Bounded at collection (``services/scan_inputs.py``): strings clipped to 512
  chars, at most 20 tool and author entries. The value is a fixed handful of
  fields, not a list that grows with the document.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("input_document", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("downgrade is not supported")
