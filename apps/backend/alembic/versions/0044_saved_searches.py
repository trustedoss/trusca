# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""saved searches — a user's parked search filters (S3)

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-31

Phase: S3 (full search page)
Kind: schema (additive — one new table; no data migration)
Forward-only: yes

What:
  - ``saved_searches``: id, user_id (FK users ON DELETE CASCADE), name,
    kind, params (JSONB), created_at.
  - UNIQUE (user_id, name) — names are how a user tells their saved searches
    apart, so a duplicate is a mistake, not a second row.
  - INDEX (user_id, created_at) — every read is "mine, newest first".

Why the params column is opaque JSONB:
  It holds whatever query string the search page carried when the user pressed
  save, replayed verbatim on open. Giving it a schema would mean this table
  knowing every filter the page will ever grow, and going stale the first time
  one is added.

Why user-scoped and not team-scoped:
  A saved search is a bookmark. The results it produces are re-run through the
  caller's own team scope every time, so sharing the row would share the filter
  text, not the findings. Team-visible saved searches are a separate feature
  with a separate permission question; this table does not prejudge it.

Migration policy (CLAUDE.md §6):
  - Additive only: a new table, no changes to existing ones.
  - Forward-only: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_saved_searches_user_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_saved_searches_user_name"),
    )
    op.create_index(
        "ix_saved_searches_user_created",
        "saved_searches",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
