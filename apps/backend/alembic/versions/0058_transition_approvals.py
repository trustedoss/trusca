# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""transition_approvals + gate_policies.approval_required_statuses

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-18

Phase: risk acceptance needs two people (N14)
Kind: schema (additive: one table, one nullable column; no data migration)
Forward-only: yes

What:
  ``gate_policies.approval_required_statuses``: which finding statuses may not
  be reached by one person alone, as a JSON array of status names. NULL means
  no transition needs a second person, which is how the product behaves today.

  ``transition_approvals``: one pending or decided request to move a finding
  into such a status, carrying who asked, why, who decided, when, and whether
  the agreed change has been applied.

Why:
  Closing a vulnerability as suppressed or not-affected ends the obligation
  without fixing anything. Today whoever holds the grade can do that alone and
  the record shows only the outcome. An organization that wants a second pair
  of eyes has no way to ask for one, and an audit asking "who agreed to accept
  this" has only one name to find.

  Which statuses count is deliberately not decided here. Accepting risk means
  different things to different organizations, and the ones that care will name
  their own list; the ones that do not leave it NULL and nothing changes.

Reversal:
  Forward-only. Both additions are inert until a policy names a status, so a
  mistaken merge changes no behaviour: no rows are written and the column reads
  as NULL everywhere.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gate_policies",
        sa.Column(
            "approval_required_statuses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.create_table(
        "transition_approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vulnerability_findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_status", sa.String(length=32), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        # Set when the agreed transition actually lands. An approval is spent
        # once: without this the same row would authorise the same change again
        # every time it was replayed, and there would be no way to tell an
        # agreement that took effect from one that was interrupted.
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'rejected')",
            name="ck_transition_approvals_state",
        ),
    )
    # One open request per finding. A second request while one is pending would
    # let two people queue conflicting outcomes for the same finding, and the
    # approver would have no way to tell which they were deciding.
    op.create_index(
        "uq_transition_approvals_open",
        "transition_approvals",
        ["finding_id"],
        unique=True,
        postgresql_where=sa.text("state = 'pending'"),
    )
    op.create_index(
        "ix_transition_approvals_team_state",
        "transition_approvals",
        ["team_id", "state"],
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
