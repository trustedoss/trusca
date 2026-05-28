"""license_fetch_cache — TTL cache for the multi-ecosystem license fetcher

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-07

Phase: 2 (chore PR #5)
PR: chore PR #5
Kind: schema
Forward-only: yes

What:
  - Create the ``license_fetch_cache`` table:
      purl           TEXT PRIMARY KEY
      spdx_id        TEXT NULL
      reference_url  TEXT NULL
      source         TEXT NOT NULL
      is_negative    BOOLEAN NOT NULL DEFAULT false
      fetched_at     TIMESTAMPTZ NOT NULL DEFAULT now()
  - Add ``ix_license_fetch_cache_fetched_at`` index on ``fetched_at``
    so a future TTL sweeper (Celery Beat) can drop expired rows in
    O(log n) without a sequential scan.

Why:
  - chore PR #5 Part B (`docs/sessions/_next-session-prompt-chore-pr5.md`)
    introduces a multi-ecosystem license fetcher that hits Maven Central /
    PyPI / crates.io / pkg.go.dev to fill in licences cdxgen could not
    extract. Each lookup is keyed on the versioned PURL — that string is
    stable across scans and is what we cache.
  - 24h TTL is enforced *in code* by the dispatcher reading ``fetched_at``;
    making the column a plain timestamp (vs. a triggered eviction) keeps
    the migration trivial and the eviction policy easy to evolve.
  - Negative caching (404 / unmapped) lives in the same table with
    ``is_negative=true`` + ``spdx_id=NULL``. Same TTL applies, so a
    repeated scan over a project full of unresolvable PURLs does not
    issue duplicate external HTTP requests within the window.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``. Dropping the cache is a manual / scripted
    op (`DROP TABLE license_fetch_cache CASCADE`); we do not codify
    rollback because the table is purely advisory.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "license_fetch_cache",
        sa.Column("purl", sa.Text(), nullable=False),
        sa.Column("spdx_id", sa.Text(), nullable=True),
        sa.Column("reference_url", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "is_negative",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("purl", name="pk_license_fetch_cache"),
    )
    op.create_index(
        "ix_license_fetch_cache_fetched_at",
        "license_fetch_cache",
        ["fetched_at"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
