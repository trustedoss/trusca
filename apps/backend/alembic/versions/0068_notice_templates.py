# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""notice_templates

Revision ID: 0068
Revises: 0067
Create Date: 2026-08-22

Phase: organization boilerplate for the NOTICE attribution document (N21)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  Plain-text preface/footer an organization writes once per NOTICE format
  (text/markdown/html). One optional row per (organization, format).

Why:
  The NOTICE has always taken only the scan's own data as input, with no
  place for an organization's letterhead or a standard legal disclaimer.
  Plain text rather than markup, and no conditional/loop template language:
  what goes in the document (the license/component/obligation list) stays
  fixed, only the boilerplate around it is configurable.

Reversal:
  Forward-only, and additive. With no rows written, every NOTICE renders
  exactly as it did before this table existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notice_templates",
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
        ),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("preface", sa.Text(), nullable=True),
        sa.Column("footer", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("organization_id", "format", name="uq_notice_templates_org_format"),
        sa.CheckConstraint(
            "format IN ('text', 'markdown', 'html')",
            name="ck_notice_templates_format",
        ),
    )


def downgrade() -> None:
    """Forward-only (CLAUDE.md 마이그레이션 정책)."""
    raise NotImplementedError("0068 is forward-only")
