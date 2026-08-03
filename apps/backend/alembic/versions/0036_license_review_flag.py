"""licenses AI review-flag column + partial index — AI license flag DB layer

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-03

Phase: D (AI-specific license flags — DB layer)
PR: feat/ai-license-flags
Kind: schema (additive — expand step; no data migration)
Forward-only: yes

What:
  - Add one column to the existing ``licenses`` table::
      review_flag  VARCHAR(24) NULL   -- behavioral_use | non_commercial | NULL
  - Add a PARTIAL index on the flagged rows::

        CREATE INDEX ix_licenses_review_flag
            ON licenses (review_flag)
            WHERE review_flag IS NOT NULL;

Why:
  - The AI-restriction class (RAIL/OpenRAIL/Llama/Gemma community, CC-BY-NC…)
    is a pure function of the license's identity (spdx_id / name), not of any
    scan or per-finding observation — the same shape as the existing
    ``category`` verdict. It therefore lives on ``License`` (the catalog),
    classified once per license by services.license_flags, rather than being
    duplicated on every ``license_findings`` row.
  - ``NULL`` = not in scope (no AI restriction recognised). The tool only
    surfaces the class; whether it applies to a given use is a human/legal
    judgement (mirrors BomLens ``license-flags.jq``).

Why partial (not a plain b-tree on ``review_flag``):
  - Flagged licenses are a small minority of the SPDX catalog. A full index
    would index every NULL row for no benefit; ``WHERE review_flag IS NOT
    NULL`` indexes only the rows the "list AI-review-flagged licenses" path
    reads. Same reasoning as 0034's partial index on ``kev`` and 0023's on
    ``reachable IS TRUE``.

Notes:
  - **Expand step only** (CLAUDE.md §6 expand → migrate-data → contract). The
    column is nullable with no default, so existing rows need no rewrite and
    simply read NULL until classified. Backfill of existing ``licenses`` rows
    is a SEPARATE concern kept out of this schema revision per the
    schema/data separation policy: rows re-populate naturally as scans
    re-classify licenses through services.license_flags, and a one-time
    backfill task may be run to flag the pre-existing catalog immediately.
    There is no contract step planned — ``review_flag`` stays legitimately
    NULL for licenses with no AI restriction.
  - ADD COLUMN of a nullable column with no default is metadata-only on
    PG 11+ (no table rewrite). No raw SQL → no asyncpg ``::`` bind concerns.
  - NOT created ``CONCURRENTLY``: Alembic runs each migration inside a
    transaction and ``CREATE INDEX CONCURRENTLY`` cannot run in a transaction
    block. The partial index over a freshly added all-NULL column has zero
    qualifying rows at upgrade time, so the in-transaction build is trivially
    cheap; matches the additive index revisions elsewhere in this tree
    (0007, 0015, 0023, 0034, ...).
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "licenses",
        sa.Column("review_flag", sa.String(length=24), nullable=True),
    )
    op.create_index(
        "ix_licenses_review_flag",
        "licenses",
        ["review_flag"],
        unique=False,
        postgresql_where="review_flag IS NOT NULL",
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
