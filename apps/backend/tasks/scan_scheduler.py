# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Celery task: ``trustedoss.scan_schedule_poll`` (D7, N18).

ONE fixed-interval poller (every 15 minutes, see ``tasks.celery_app``) rather
than one Beat entry per project: the row count in ``scan_schedules`` must
never become the row count in the Beat schedule. Each tick asks: which
non-archived projects are due right now, in their own configured timezone,
and have not already fired for this due window? Everything else (the
concurrency cap, the disk guard, the Scan row shape, the Celery dispatch) is
the exact sequence ``services.scan_service`` already enforces for a
webhook-triggered scan; this task supplies none of its own.

A project with no schedule of its own inherits the organization default; a
project with a schedule of its own (even ``is_active=false``) never falls
back to it. See ``services.scan_schedule_service.resolve_for_project`` for the
same fall-through expressed for the (async) API path; this task re-derives it
in one query because it must resolve every project at once, not one at a time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from core.db import sync_session_scope
from models import Project, ScanSchedule, Team
from services.scan_service import (
    capacity_guard_reason_sync,
    enqueue_system_triggered_scan_sync,
)
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.scan_scheduler")


def _is_due(schedule: ScanSchedule, now_utc: datetime) -> bool:
    """Whether *schedule* should fire at *now_utc*.

    Cadence and day-of-week are read against the schedule's OWN timezone, not
    the server's: "09:00 Monday" means the same wall-clock moment in Seoul
    that it does in London, each in its own zone. ``last_triggered_at`` is the
    only thing preventing the 15-minute poller from firing the same daily/
    weekly slot repeatedly across the hour it stays "due": a schedule that
    already fired for this local date does not fire again until its date (or,
    for weekly, its weekday) changes.
    """
    try:
        tz = ZoneInfo(schedule.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # A malformed/removed IANA name should never crash the poller: the
        # schema-level CHECK/validator already reject one on write, so this
        # only guards a zone the platform's tzdata later stopped shipping.
        log.warning(
            "scan_schedule.bad_timezone",
            schedule_id=str(schedule.id),
            timezone=schedule.timezone,
        )
        return False

    local_now = now_utc.astimezone(tz)
    if local_now.hour != schedule.hour:
        return False
    if schedule.cadence == "weekly" and local_now.weekday() != schedule.day_of_week:
        return False

    if schedule.last_triggered_at is not None:
        last_local = schedule.last_triggered_at.astimezone(tz)
        if last_local.date() == local_now.date():
            return False

    return True


def _due_targets(session: Session, now_utc: datetime) -> list[tuple[Project, ScanSchedule]]:
    """(project, governing schedule) for every due, non-archived project.

    One query with two LEFT JOINs rather than one query per project: a
    project's own row (if any) and its organization's default row (if any),
    picked in that order in Python. Archived projects are excluded up front:
    scanning them would fight the "archiving disables new scans" invariant
    (services.scan_service.ScanArchivedConflict) forever, since nothing else
    ever clears a schedule row on archive.
    """
    project_row = aliased(ScanSchedule)
    org_row = aliased(ScanSchedule)

    stmt = (
        select(Project, project_row, org_row)
        .join(Team, Team.id == Project.team_id)
        .outerjoin(project_row, project_row.project_id == Project.id)
        .outerjoin(
            org_row,
            (org_row.organization_id == Team.organization_id) & (org_row.project_id.is_(None)),
        )
        .where(Project.archived_at.is_(None))
    )

    due: list[tuple[Project, ScanSchedule]] = []
    for project, own, org_default in session.execute(stmt).all():
        effective = own if own is not None else org_default
        if effective is None:
            continue
        if not effective.is_active or effective.cadence is None:
            continue
        if _is_due(effective, now_utc):
            due.append((project, effective))
    return due


def poll_due_schedules(now_utc: datetime | None = None) -> dict[str, Any]:
    """Enqueue every project whose schedule is due right now. Returns counts.

    Extracted from the Celery task body so a unit test can call it directly
    with a fixed ``now_utc`` instead of depending on wall-clock time.
    """
    now_utc = now_utc or datetime.now(UTC)
    enqueued: list[str] = []
    skipped_active = 0
    skipped_capacity = 0
    skipped_disk = 0

    with sync_session_scope() as session:
        for project, schedule in _due_targets(session, now_utc):
            reason = capacity_guard_reason_sync(session, team_id=project.team_id)
            if reason == "skipped_team_at_capacity":
                skipped_capacity += 1
                continue
            if reason == "skipped_disk_full":
                # The workspace volume is over its hard limit for every
                # project, not just this one; stop the whole tick rather
                # than burn guard queries on projects that will all fail the
                # same way. The next poll tick tries again.
                skipped_disk += 1
                log.warning("scan_schedule.disk_full_stop")
                break

            scan_id = enqueue_system_triggered_scan_sync(
                session,
                project,
                metadata={
                    "trigger": "schedule",
                    "source": "scheduled-scan",
                    "schedule_id": str(schedule.id),
                },
            )
            if scan_id is None:
                # A scan is already queued/running for this project, so do NOT
                # stamp last_triggered_at, so the next tick (still inside the
                # same due hour) retries once that scan clears rather than
                # silently losing today's/this week's run to a transient
                # collision.
                skipped_active += 1
                continue

            schedule.last_triggered_at = now_utc
            session.commit()
            enqueued.append(str(scan_id))

    result = {
        "enqueued": enqueued,
        "enqueued_count": len(enqueued),
        "skipped_active_scan": skipped_active,
        "skipped_team_at_capacity": skipped_capacity,
        "skipped_disk_full": skipped_disk,
    }
    log.info("scan_schedule.poll_complete", **{k: v for k, v in result.items() if k != "enqueued"})
    return result


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.scan_schedule_poll",
    bind=True,
    max_retries=0,  # a missed 15-minute tick is caught by the next one
)
def scan_schedule_poll(self: Any) -> dict[str, Any]:
    return poll_due_schedules()


__all__ = ["poll_due_schedules", "scan_schedule_poll"]
