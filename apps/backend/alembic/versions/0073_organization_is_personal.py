# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""organizations: add is_personal discriminator

Revision ID: 0073
Revises: 0072
Create Date: 2026-08-30

Kind: schema (additive column with server default), no data backfill needed
Forward-only: yes

What:
  Adds ``organizations.is_personal boolean not null default false``. True
  marks an Organization created as a side effect of a signup (self-
  registration or OAuth), one per user; false marks the shared/platform org
  an installer or admin provisions.

Why:
  Security review, self-resource-validation-plan-2026-08-30.md §6-5:
  ``admin_team_service._pick_default_org`` refuses when more than one
  Organization exists, but that alone does not close the gap when exactly
  one exists and it is itself a signup's personal org (the narrow window
  right after a demo-SaaS deployment's first self-registration, before a
  second signup ever happens). A ``super_admin`` creating a team via
  ``POST /v1/admin/teams`` without ``organization_id`` during that window
  would silently attach the new team to that stranger's personal
  organization, and any member later added to it would gain visibility into
  that organization's ``OrganizationComponentVerdict`` rows. ``_pick_default_
  org`` is changed (same PR) to only auto-pick a non-personal row, so it
  needs this column to tell the two kinds of Organization apart.

Backfill:
  None needed. Every row that exists before this migration predates the
  distinction; defaulting all of them to ``false`` (platform org) preserves
  today's behaviour exactly (every existing single-org deployment already
  relied on that lone row being auto-picked as the shared one) rather than
  guessing intent retroactively. Only organizations created after this
  migration by the signup paths carry ``is_personal = true``.

Reversal:
  Forward-only. Dropping the column would be a plain
  ``ALTER TABLE ... DROP COLUMN`` if ever needed, but no downgrade path is
  provided (CLAUDE.md §6).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0073"
down_revision: str | None = "0072"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("is_personal", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0073 is forward-only")
