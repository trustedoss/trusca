# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""report_format_templates

Revision ID: 0069
Revises: 0068
Create Date: 2026-08-22

Phase: organization formatting defaults for the vulnerability PDF/HTML
report (N22)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  One optional row per organization: a plain-text header line, an
  organization label (replacing the "TRUSCA" brand text), and a column
  subset for each of the report's two tables.

Why:
  The report has always rendered a fixed set of columns and a fixed brand
  header, with no place for an organization's own label or a preference for
  which columns matter to it.

Reversal:
  Forward-only, and additive. With no row written, every report renders
  exactly as it did before this table existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_format_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("header_text", sa.Text(), nullable=True),
        sa.Column("org_label", sa.String(length=200), nullable=True),
        sa.Column("vulnerability_columns", postgresql.JSONB(), nullable=True),
        sa.Column("component_columns", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Forward-only (CLAUDE.md 마이그레이션 정책)."""
    raise NotImplementedError("0069 is forward-only")
