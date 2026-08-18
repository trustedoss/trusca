# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""organization_component_verdicts

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-18

Phase: an organization can rule on a component once (N12)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  One organization-wide ruling per component, with the same four states and
  the same optimistic-concurrency version the per-project approvals already
  use, so an operator moving between the two surfaces meets one vocabulary.

Why:
  A component that thirty projects depend on is reviewed thirty times today,
  and the answer is usually the same each time because the question is about
  the component, not the project. An organization needs somewhere to record
  that answer once.

  A separate table rather than a nullable ``project_id`` on
  ``component_approvals``. Widening that column would leave the existing
  partial unique index (component_id, project_id) not covering the new rows,
  because SQL treats NULLs as distinct, so "one open approval per component
  and project" would quietly stop meaning what it says. ``team_id`` is also
  NOT NULL there and an organization ruling has no team to put in it. The
  cost of a separate table is a second place that models a decision; the cost
  of the other way is an invariant that reads as intact and is not.

  Nothing constrains the two against each other. A project may keep its own
  review open while the organization rules, and the project's answer wins
  where it exists. The fallback happens at read time only, so neither surface
  has to know about the other's row lifecycle.

Reversal:
  Forward-only, and inert until something reads it. The existing index is
  untouched, so a mistaken merge changes no per-project behaviour: reverting
  the code that reads this table restores the previous system exactly, with
  no contract step to undo.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organization_component_verdicts",
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
        sa.Column(
            "component_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("components.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The same native ENUM the per-project approvals use. A second type
        # with the same four names would be two vocabularies for one idea, and
        # the first divergence would be invisible until a report joined them.
        sa.Column(
            "status",
            postgresql.ENUM(name="approval_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        # Required, unlike the per-project note. A ruling that reaches every
        # project in the organization is the one people will ask about later,
        # and "why" is the whole of what they will be asking.
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        # Mirrors component_approvals.version: the API hands it out as an ETag
        # and demands it back, so two administrators deciding at once produce a
        # 412 rather than a lost write.
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
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
        # A decided row without a timestamp sorts last, so it would lose to
        # every other decision and the organization's actual answer would go
        # unread. Nothing writes such a row today; this stops a backfill or a
        # data migration from introducing one silently.
        sa.CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR decided_at IS NOT NULL",
            name="ck_org_component_verdicts_decided_at",
        ),
    )
    # One open ruling per organization and component, the same shape as the
    # per-project constraint. Once decided the row is terminal and a fresh
    # ruling may be opened, which is how an organization changes its mind
    # without anybody editing the record of what it used to think.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_org_component_verdicts_unique_open
        ON organization_component_verdicts (organization_id, component_id)
        WHERE status IN ('pending', 'under_review')
        """
    )
    op.create_index(
        "ix_org_component_verdicts_org_status",
        "organization_component_verdicts",
        ["organization_id", "status"],
    )
    op.create_index(
        "ix_org_component_verdicts_component",
        "organization_component_verdicts",
        ["component_id"],
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
