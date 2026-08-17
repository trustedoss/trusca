# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""gate_policies

Revision ID: 0057
Revises: 0056
Create Date: 2026-08-18

Phase: policy as rows (N8)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  ``gate_policies``: what blocks a build, scoped to an organization with an
  optional per-team override. Same arrangement as ``license_policies``, down
  to the partial unique index that allows exactly one org-default row.

Why:
  The build gate could already be tuned, but only through environment
  variables: changing it needed access to the deployment and left no record of
  who decided it. Security policy is set by people who do not deploy, and it
  is the kind of decision an audit asks about later.

  Every column is nullable on purpose. NULL means "not decided at this scope",
  so a lookup falls through team, then organization, then the environment
  variable, then the built-in default. A deployment with no rows behaves
  exactly as it did before, which is what makes the table safe to add before
  anything writes to it.

Reversal:
  Forward-only. The table is additive and unread until the resolver lands, so
  a mistaken merge is inert rather than harmful: nothing writes rows, and
  nothing reads them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gate_policies",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("epss_threshold", sa.Float(), nullable=True),
        sa.Column("reachable_critical_only", sa.Boolean(), nullable=True),
        sa.Column("malicious_blocks", sa.Boolean(), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
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
        sa.UniqueConstraint("organization_id", "team_id", name="uq_gate_policies_org_team"),
        sa.CheckConstraint(
            "epss_threshold IS NULL OR (epss_threshold >= 0 AND epss_threshold <= 1)",
            name="ck_gate_policies_epss_range",
        ),
    )
    # Postgres treats NULLs as distinct, so the unique constraint above does
    # not stop a second org-default row. This does.
    op.create_index(
        "uq_gate_policies_org_default",
        "gate_policies",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("team_id IS NULL"),
    )
    op.create_index("ix_gate_policies_team_id", "gate_policies", ["team_id"])


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
