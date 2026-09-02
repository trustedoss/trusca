# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""task_runs: per-execution history for background tasks

Revision ID: 0075
Revises: 0074
Create Date: 2026-09-03

Kind: schema (new table), no data backfill
Forward-only: yes

What:
    Adds ``task_runs``, one row per background task execution, plus three
    indexes for the access patterns that motivated it.

Why:
    Seventeen periodic tasks run here; five record anything, and those five
    write singleton sync-state rows holding only the latest tick. "How many
    times has this failed", "how long has it been failing" and "what did it
    delete last night" have had no answer.

Notes:
    ``outcome`` is nullable on purpose. NULL means the run started and has not
    reported an end: either it is in flight, or the worker died before
    ``task_postrun`` could fire. Both are states an operator needs to see, and
    a stale NULL is itself the finding. The check constraint therefore allows
    NULL alongside the three terminal values.

    ``attempt`` starts at 1 and each retry writes its own row rather than
    incrementing a counter, so a third failure that differs from the first two
    is still visible. Retries keep the Celery task id, which is what groups
    them; hence the index on that column.

    No foreign keys. This table outlives the things it describes (a run of a
    retention sweep is worth reading after the rows it deleted are gone), and
    it is written from a signal handler that must not fail the task it is
    recording. A cascade or a constraint violation there would do exactly
    that.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "task_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("task_name", sa.String(length=128), nullable=False),
        sa.Column("celery_task_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "attempt", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("skipped_reason", sa.String(length=64), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'failed', 'skipped')",
            name="ck_task_runs_outcome",
        ),
        sa.CheckConstraint("attempt >= 1", name="ck_task_runs_attempt_positive"),
    )
    op.create_index(
        "ix_task_runs_name_started",
        "task_runs",
        ["task_name", sa.text("started_at DESC")],
    )
    op.create_index("ix_task_runs_started_at", "task_runs", ["started_at"])
    op.create_index(
        "ix_task_runs_celery_task_id", "task_runs", ["celery_task_id"]
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0075 is forward-only")
