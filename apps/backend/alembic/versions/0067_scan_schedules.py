# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""scan_schedules

Revision ID: 0067
Revises: 0066
Create Date: 2026-08-22

Phase: scans that start themselves, on a cadence the deployment writes (N18)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  A project's own scan cadence, or the organization's default. One optional
  row per project (``project_id`` set), one optional organization-default row
  (``project_id`` NULL), the same pairing ``gate_policies`` and
  ``license_policies`` already use for their own org-default row.

Why:
  Nothing today starts a scan on its own; every trigger is a person, a CI
  job, or a webhook delivery. An organization that wants its projects rescanned
  on a cadence has had to run one itself against the scan-trigger API. This
  table is where that cadence becomes something the deployment remembers
  and a fixed-interval Celery beat poller (not one beat entry per project)
  reads.

Reversal:
  Forward-only, and additive. With no rows written, nothing polls into an
  automatic scan and the deployment behaves exactly as it did before this
  table existed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scan_schedules",
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
        # NULL -> the organization default; non-NULL -> that project's override.
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("cadence", sa.String(length=16), nullable=True),
        sa.Column("hour", sa.Integer(), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default=sa.text("'UTC'")
        ),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("organization_id", "project_id", name="uq_scan_schedules_org_project"),
        sa.CheckConstraint(
            "cadence IS NULL OR cadence IN ('daily', 'weekly')",
            name="ck_scan_schedules_cadence",
        ),
        sa.CheckConstraint(
            "hour IS NULL OR (hour >= 0 AND hour <= 23)",
            name="ck_scan_schedules_hour_range",
        ),
        sa.CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="ck_scan_schedules_day_of_week_range",
        ),
        sa.CheckConstraint(
            "cadence <> 'weekly' OR day_of_week IS NOT NULL",
            name="ck_scan_schedules_weekly_requires_day",
        ),
        sa.CheckConstraint(
            "cadence <> 'daily' OR day_of_week IS NULL",
            name="ck_scan_schedules_daily_forbids_day",
        ),
    )
    # Postgres treats NULLs as distinct, so the UNIQUE constraint above does
    # not stop two org-default (project_id IS NULL) rows. This partial index
    # does, the same pairing gate_policies/license_policies use.
    op.create_index(
        "uq_scan_schedules_org_default",
        "scan_schedules",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index("ix_scan_schedules_project_id", "scan_schedules", ["project_id"])


def downgrade() -> None:
    """Forward-only (CLAUDE.md 마이그레이션 정책)."""
    raise NotImplementedError("0067 is forward-only")
