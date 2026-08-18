# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""projects: organizational attributes and distribution model

Revision ID: 0060
Revises: 0059
Create Date: 2026-08-18

Phase: a project can say who owns it and how it ships (N16)
Kind: schema (additive: three nullable columns; no data migration)
Forward-only: yes

What:
  ``business_unit``  which part of the organization owns this project.
  ``owner_contact``  who to ask about it.
  ``distribution_model``  how the software reaches the people who use it.

Why:
  A portfolio of two hundred projects is a list of names until somebody can
  say "the ones my division owns" or "the ones we actually ship". The first
  two are free text because a division, a cost centre and a squad are all
  the same slot to different organizations, and a fixed vocabulary would be
  wrong for most of them. The third is a closed set because it is not an
  organizational label: it is what decides which licence obligations bind,
  and a typo there has to be a rejection rather than a new category.

  All three are NULL by default and NULL keeps today's behaviour. For the
  distribution model that is the conservative reading rather than an absence:
  a project that has not said how it ships is judged as though it ships every
  way, which is what the portal already assumes. ``ai_usage_context`` on the
  same table works exactly this way (models/scan.py) and this follows it.

Reversal:
  Forward-only, and inert until something writes them. Nothing reads these to
  produce a verdict yet, so a mistaken merge changes no judgement anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("business_unit", sa.String(length=120), nullable=True))
    op.add_column("projects", sa.Column("owner_contact", sa.String(length=255), nullable=True))
    op.add_column(
        "projects",
        sa.Column("distribution_model", sa.String(length=32), nullable=True),
    )
    # The filter the portfolio page offers. Partial, because the rows worth
    # narrowing to are the ones that said something, and a NULL-heavy index
    # would be mostly the projects nobody filtered for.
    op.create_index(
        "ix_projects_business_unit",
        "projects",
        ["business_unit"],
        postgresql_where=sa.text("business_unit IS NOT NULL"),
    )
    op.create_index(
        "ix_projects_distribution_model",
        "projects",
        ["distribution_model"],
        postgresql_where=sa.text("distribution_model IS NOT NULL"),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
