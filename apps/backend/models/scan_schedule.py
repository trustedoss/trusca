# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Scheduled scans (N18): a project's own cadence, or the organization's default.

No row anywhere means no automatic scan, so the deployment behaves exactly as
it did before this table existed, and an operator opts in per project or for
the whole organization at once.

Scoping mirrors ``gate_policies`` deliberately: one org-default row with
``project_id IS NULL``, and one optional row per project that overrides it. A
project row is authoritative the moment it exists, active or not: writing one
with ``is_active=false`` is how a project opts out of an organization default
without deleting anything, which is why ``is_active`` and ``cadence`` are both
independently nullable rather than the row's mere existence being the signal.

The columns describe a fixed local time, not a cron expression: this is the
first schedule TRUSCA ships, one clock reading a day (``daily``) or a week
(``weekly``) is the whole requirement, and a cron parser is a dependency this
does not need yet. ``timezone`` is the IANA name the hour and weekday are read
against, so "09:00 Monday" means the same wall-clock moment in Seoul that it
does in London, each in its own zone.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

#: Closed cadence vocabulary. Plain ``String`` + CHECK rather than a native
#: Postgres ENUM: with only two values and no other table referencing this
#: vocabulary yet, a migration-owned ENUM type buys nothing a CHECK does not
#: already give, and it is one less type to widen if a third cadence appears.
SCAN_SCHEDULE_CADENCE_VALUES = ("daily", "weekly")


class ScanSchedule(Base):
    """One scan cadence: a project's own, or the organization default."""

    __tablename__ = "scan_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL -> the organization default; non-NULL -> that project's override.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )

    # A project row is authoritative regardless of this flag: false means
    # "this project explicitly runs no automatic scan", not "ignore this row
    # and fall back to the organization default". Only the ABSENCE of a
    # project row falls through.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # NULL alongside is_active=false is the "written but not yet configured,
    # or deliberately blank" state; it resolves to no schedule either way.
    cadence: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # Local hour-of-day (0-23) the schedule fires, read in `timezone`.
    hour: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 0=Monday .. 6=Sunday (``datetime.weekday()`` convention). Required when
    # cadence='weekly', forbidden (must stay NULL) when cadence='daily': a
    # daily schedule that also carried a day-of-week would raise the question
    # of which one wins the day it disagrees with itself.
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # IANA zone name (e.g. "UTC", "Asia/Seoul"). hour/day_of_week are local to
    # this zone, not to the server's.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))

    # Stamped by the poller after it enqueues a scan for this row, in UTC.
    # Read back to decide "have I already fired for today/this week": the
    # poller runs far more often than the schedule itself, so this is the only
    # thing standing between one due window and a scan every poll tick.
    last_triggered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "project_id", name="uq_scan_schedules_org_project"),
        # Postgres treats NULLs as distinct, so the constraint above does not
        # stop two org-default rows. This does, the same pairing
        # gate_policies and license_policies use for their own org-default row.
        Index(
            "uq_scan_schedules_org_default",
            "organization_id",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
        ),
        Index("ix_scan_schedules_project_id", "project_id"),
        CheckConstraint(
            "cadence IS NULL OR cadence IN ('daily', 'weekly')",
            name="ck_scan_schedules_cadence",
        ),
        CheckConstraint(
            "hour IS NULL OR (hour >= 0 AND hour <= 23)",
            name="ck_scan_schedules_hour_range",
        ),
        CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="ck_scan_schedules_day_of_week_range",
        ),
        CheckConstraint(
            "cadence <> 'weekly' OR day_of_week IS NOT NULL",
            name="ck_scan_schedules_weekly_requires_day",
        ),
        CheckConstraint(
            "cadence <> 'daily' OR day_of_week IS NULL",
            name="ck_scan_schedules_daily_forbids_day",
        ),
    )


__all__ = ["SCAN_SCHEDULE_CADENCE_VALUES", "ScanSchedule"]
