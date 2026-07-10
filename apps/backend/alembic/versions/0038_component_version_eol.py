"""component_versions end-of-life (EOL) columns + partial index — EOL DB layer

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-11

Phase: EOL (endoflife.date end-of-life flagging — DB layer, Phase M)
PR: feat/eol-catalog
Kind: schema (additive — expand step; no data migration)
Forward-only: yes

What:
  - Add six columns to the existing ``component_versions`` table::

      eol_state        VARCHAR(16) NULL   -- 'eol' | 'supported' | 'unknown'
      eol_product      VARCHAR(64) NULL   -- endoflife.date product slug
      eol_cycle        VARCHAR(32) NULL   -- derived release cycle ('3.2')
      eol_date         DATE NULL          -- published EOL date (when dated)
      eol_source       VARCHAR(64) NULL   -- 'endoflife.date@YYYY-MM-DD'
      eol_evaluated_at TIMESTAMPTZ NULL   -- last stamp time

  - Add a PARTIAL index on the flagged rows::

        CREATE INDEX ix_component_versions_eol
            ON component_versions (eol_state)
            WHERE eol_state = 'eol';

Why:
  - End-of-life is a fact about *product + release cycle* — identical for
    every scan and project observing the same ``purl_with_version``. That is
    the KEV shape (0034: ``kev`` lives on the shared ``vulnerabilities``
    catalog, not per-scan ``vulnerability_findings``), so EOL lives on the
    shared ``component_versions`` catalog, not on ``scan_components``.
    Catalog storage keeps re-evaluation possible: a newer endoflife.date
    snapshot re-stamps existing rows (tasks/eol_catalog_refresh, Phase M
    PR-3) exactly like kev_catalog_refresh re-stamps ``vulnerabilities``.
  - All columns NULLable — NULL means "never evaluated / not a tracked
    product", mirroring the enrichment's closed-whitelist philosophy (an
    unmapped component is left untouched, never guessed). ``eol_state`` is
    VARCHAR, not a native ENUM (same reasoning as ``kev_sync_state``'s
    ``last_result``: a closed vocabulary enforced at the application layer
    keeps additive vocabulary changes migration-free).

Why partial (not a plain b-tree on ``eol_state``):
  - Tracked products are a curated ~10-rule whitelist of frameworks; EOL
    rows are a tiny minority of the cross-project component catalog. The
    ``?eol=true`` filter and the overview/health counts read only flagged
    rows — ``WHERE eol_state = 'eol'`` indexes exactly that set (same
    reasoning as 0034's ``WHERE kev``).

Notes:
  - **Expand step only** (CLAUDE.md §6). Nullable ADD COLUMNs are
    metadata-only on PG 11+ (no rewrite); the partial index covers zero
    rows at upgrade time. The data fill happens at persist time
    (persist_sbom_components) and via the refresh task — kept out of this
    schema revision per the schema/data separation policy.
  - NOT created ``CONCURRENTLY`` — Alembic migrations run in a transaction
    and the index covers zero qualifying rows at build time (0034 note).
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "component_versions",
        sa.Column("eol_state", sa.String(16), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("eol_product", sa.String(64), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("eol_cycle", sa.String(32), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("eol_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("eol_source", sa.String(64), nullable=True),
    )
    op.add_column(
        "component_versions",
        sa.Column("eol_evaluated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_component_versions_eol",
        "component_versions",
        ["eol_state"],
        unique=False,
        postgresql_where=sa.text("eol_state = 'eol'"),
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CLAUDE.md §6)")
