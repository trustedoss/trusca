# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""obligation_fulfilments

Revision ID: 0064
Revises: 0063
Create Date: 2026-08-19

Phase: somewhere to record that an obligation was actually met (N15)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  One row per project per obligation, recording who is doing it, by when,
  what state it is in, and what evidence there is.

Why:
  The portal can say a licence obliges you to publish a notice, and it can
  generate the notice. It cannot say whether anybody did it, so the answer to
  "are we compliant" lives in a spreadsheet somebody keeps beside the tool.

  No toggle on this one. Every organization that has an obligation has to be
  able to show it was met, so an installation where this is off is one where
  the record lives somewhere the portal cannot see.

  Deliberately a table beside the catalog rather than columns on it. The
  obligations themselves are shared: one row describes what Apache-2.0 asks
  of everybody, and a project's progress against it is not a property of the
  licence.

Reversal:
  Forward-only, and additive. The obligation reads keep their existing shape
  with the fulfilment attached alongside, so reverting the code that reads
  this table leaves those responses exactly as they were.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "obligation_fulfilments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "obligation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("obligations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Denormalised from the project so the queue can be scoped without a
        # join on every poll, the same way the other work queues carry it.
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'not_started'"),
        ),
        sa.Column(
            "assignee_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("due_on", sa.Date(), nullable=True),
        # What was done, in the words of whoever did it, and where to look.
        # A note rather than an upload: the evidence is usually somewhere that
        # already exists (a release page, a repository file, a ticket), and
        # copying it here would make the portal the second place it can be
        # wrong.
        sa.Column("evidence_note", sa.Text(), nullable=True),
        sa.Column("evidence_url", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "completed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.CheckConstraint(
            "status IN ('not_started', 'in_progress', 'done', 'not_applicable')",
            name="ck_obligation_fulfilments_status",
        ),
        # A row marked done says somebody finished it, so it has to say when.
        sa.CheckConstraint(
            "status <> 'done' OR completed_at IS NOT NULL",
            name="ck_obligation_fulfilments_completed_at",
        ),
        sa.UniqueConstraint(
            "project_id",
            "obligation_id",
            name="uq_obligation_fulfilments_project_obligation",
        ),
    )
    op.create_index(
        "ix_obligation_fulfilments_team_status",
        "obligation_fulfilments",
        ["team_id", "status"],
    )
    op.create_index(
        "ix_obligation_fulfilments_assignee",
        "obligation_fulfilments",
        ["assignee_user_id"],
        postgresql_where=sa.text("assignee_user_id IS NOT NULL"),
    )
    # "what is overdue" is the question this table exists to answer.
    op.create_index(
        "ix_obligation_fulfilments_due",
        "obligation_fulfilments",
        ["due_on"],
        postgresql_where=sa.text(
            "due_on IS NOT NULL AND status <> 'done' AND status <> 'not_applicable'"
        ),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
