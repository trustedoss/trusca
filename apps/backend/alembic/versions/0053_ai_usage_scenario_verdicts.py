# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""judge AI model licenses against an intended use

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-13

Phase: AI usage-scenario license verdicts (gap #28)
Kind: schema (additive: two nullable columns; no data migration)
Forward-only: yes

What:
  ``projects.ai_usage_context``, how this project intends to use the models it
  carries: internal | product | redistribute | outputs-only. Nullable.

  ``sbom_conformance.ai_subjects``, what an ingested document said about its
  models and datasets: their names, the license strings on them, and which
  datasets each model declares a dependency on. Nullable JSONB.

Why the facts are stored but the verdict is not:
  The verdict depends on the scenario, and the scenario is a setting an operator
  changes. A verdict written at ingest would be stale the moment they did, and
  and nothing would notice: the row would keep answering with yesterday's intent.
  What the document says does not change after ingest, so that is what is kept,
  and ``services/ai_risk_assessment.assess`` turns it into verdicts on read.

Why NULL is meaningful, twice:
  A NULL ``ai_usage_context`` is not "unset, assume the safest use": it means
  judge against the full terms, which IS the conservative reading. A NULL
  ``ai_subjects`` means the document carried no machine-learning-model
  component, so there is nothing this axis has an opinion about.

Sizing:
  Bounded at collection (``services/ai_risk_assessment.extract_subjects``): at
  most 200 subjects, 20 license strings each, strings clipped to 512 chars. Real
  ML-BOMs are far below all three; the bounds exist so a malformed document
  cannot make one column unbounded.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("ai_usage_context", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "sbom_conformance",
        sa.Column("ai_subjects", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("downgrade is not supported")
