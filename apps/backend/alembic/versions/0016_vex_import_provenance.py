"""vulnerability_findings VEX-import provenance columns — v2.1 Track A (A2)

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-24

Phase: v2.1 (Track A — A2 VEX import / consume)
PR: feat/v2.1-vex-import
Kind: schema (additive — expand step; no data migration)
Forward-only: yes

What:
  - Add two nullable columns to the existing ``vulnerability_findings`` table::
      analysis_source  TEXT  NULL   -- 'manual' | 'vex_import' (app-enforced)
      vex_origin       JSONB NULL   -- provenance of the consuming VEX document

Why:
  - A2 lets an analyst upload an external VEX document (OpenVEX / CycloneDX
    VEX) whose statements auto-transition matching findings, suppressing
    triage noise. When a status came from a VEX import (rather than a manual
    UI transition) we must be able to tell the two apart for the audit trail,
    for round-trip stability, and so a later "re-import" can recognise its own
    prior effect (idempotency).
  - ``analysis_source`` records *who/what* drove the last status mutation:
    ``'manual'`` for the existing PATCH workflow, ``'vex_import'`` for A2. It is
    left NULL on legacy rows (status set before A2 shipped); the API treats
    NULL the same as ``'manual'`` for display.
  - ``vex_origin`` records the *document* that drove the import: the OpenVEX
    ``@id`` / CycloneDX ``serialNumber``, author, document timestamp, and the
    specific VEX status the statement carried. Keeping it as JSONB (not a set
    of typed columns) keeps the two VEX dialects' differing provenance shapes
    in one nullable field without a wide, mostly-NULL column set.

Notes:
  - **Expand step only** (CLAUDE.md §6 expand → migrate-data → contract).
    Both columns are NULLABLE with no server default: existing rows stay NULL,
    and nothing backfills them (legacy status transitions have no recoverable
    source). There is no contract step planned — NULL is a legitimate,
    permanent value meaning "pre-A2 / manual".
  - ``analysis_source`` is a free TEXT column, not a Postgres ENUM, on purpose:
    the value set is app-enforced (services/vex_import + vulnerability_service)
    and we want to avoid an ``ALTER TYPE ADD VALUE`` migration if a future
    source (e.g. ``'ci_gate'``) appears. A CHECK constraint is intentionally
    omitted for the same forward-compatibility reason; the writers are the
    single point that sets the value.
  - ``vex_origin`` is JSONB (not JSON) so a future query can index into it
    (e.g. "all findings consumed from document X") with a GIN index in a later
    revision if needed. No index is added now — the column is write-mostly and
    A2 never queries by it.
  - Pure additive DDL (ADD COLUMN NULL is metadata-only on PG 11+; no table
    rewrite, no backfill scan). No raw SQL → no asyncpg ``::`` / TIMESTAMPTZ
    bind concerns.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "vulnerability_findings",
        sa.Column("analysis_source", sa.Text(), nullable=True),
    )
    op.add_column(
        "vulnerability_findings",
        sa.Column("vex_origin", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
