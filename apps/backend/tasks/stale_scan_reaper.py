# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Stale running-scan reaper - Celery Beat.

A scan task marks its own row terminal on every in-process exit path, and
that covers success, ordinary failure and the soft time limit. It does not
cover the worker not being there any more:

  - **Hard time limit (SIGKILL)** - uncatchable, no handler runs.
  - **Worker crash / OOM kill / container restart** - the process vanishes
    mid-scan. A ``docker restart`` of the daemon does this to every worker at
    once.

``core.config.scan_hard_time_limit_seconds`` already names the consequence in
its own docstring: the scan is "stuck in ``running`` forever". Nothing acted
on it. ``tasks.workspace_cleaner`` reclaims the abandoned *directory* but
deliberately never touches the row, so the row outlives the disk it described.

A row stuck in ``running`` is not cosmetic. It holds:

  - the ``ix_scans_project_active`` partial unique index slot, so that project
    and ref can never be scanned again - every retry gets a 409;
  - a slot against ``SCAN_CONCURRENCY_CAP_PER_TEAM``, so the team's capacity
    shrinks by one permanently.

Both are silent. The operator sees a project that refuses to scan and a team
that is mysteriously slow, with nothing in either UI explaining why.

Why the age bar is the hard time limit
--------------------------------------
Queue wait is unbounded under backlog, but *execution* is not: Celery SIGKILLs
a scan task at ``SCAN_HARD_TIME_LIMIT_SECONDS``. So a row that says ``running``
and has not been written to for longer than that limit cannot belong to a live
task, whatever the deployment's timings are. The grace period on top is only
there so the reaper never races the soft-limit handler's own ``mark_failed``.

Why ``queued`` needs a different test (ER11)
-------------------------------------------
``queued`` used to be left alone on the grounds that sitting for hours is a
backlog rather than a fault. That is right about age and wrong about the
outcome: when the broker loses the message (a Redis restart, an eviction)
nothing redelivers it, and the row waits forever holding the same two things
a stuck ``running`` row holds. One Redis restart can leave a project unable
to scan until someone edits the database by hand.

Age cannot separate the two cases, because a queue list has no bound and a
genuinely backlogged scan may legitimately wait longer than any threshold. So
the reaper asks the broker instead: ``tasks._broker_inventory`` lists every
task id still held in a queue list or in ``unacked``, and only a row the
broker has no message for is reclaimed. When the broker cannot be read the
queued pass is skipped entirely, because reaping a live scan is worse than
leaving a dead row.

The grace period below is not the test for death; it only keeps the reaper out
of the window between the row being committed and its task being published,
where the broker has legitimately not heard of it yet.

Liveness is read as the LATEST of ``started_at`` and ``updated_at``: any write
to the row (a progress step, a stage change) proves the task was alive at that
moment, and taking the later of the two can only make the reaper more
cautious.

CLAUDE.md rule #11: the limit and the grace period are read at call time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select

from core.config import (
    scan_hard_time_limit_seconds,
    stale_queued_scan_grace_seconds,
    stale_running_scan_grace_seconds,
)
from core.db import sync_session_scope
from models import Scan
from tasks._broker_inventory import broker_known_task_ids
from tasks._scan_pipeline import mark_failed
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.stale_scan_reaper")

#: Cap on rows reaped per pass. A daemon restart can strand every scan that
#: was running at once, and each reap commits and fans out a notification, so
#: the pass is bounded and the next one six-hourly... in practice half-hourly,
#: picks up the rest. Without a bound one pass could hold a worker slot for a
#: long time doing notification I/O.
_MAX_REAPED_PER_PASS = 200


def _reap_orphaned_queued_scans() -> list[str]:
    """Fail ``queued`` scans the broker is no longer holding a message for.

    Returns the reclaimed scan ids. Returns an empty list without touching the
    database whenever the broker inventory is unavailable, which is the
    fail-safe direction: a scan that is actually waiting must never be killed.
    """
    known = broker_known_task_ids()
    if known is None:
        log.info("orphaned_queued_scan_scan_skipped", reason="broker_unreadable")
        return []

    grace_seconds = stale_queued_scan_grace_seconds()
    cutoff = datetime.now(UTC) - timedelta(seconds=grace_seconds)

    orphaned: list[str] = []
    with sync_session_scope() as session:
        stmt = (
            select(Scan)
            .where(Scan.status == "queued")
            .where(Scan.created_at < cutoff)
            .order_by(Scan.created_at)
            .limit(_MAX_REAPED_PER_PASS)
        )
        for scan in session.execute(stmt).scalars().all():
            task_id = scan.celery_task_id
            if task_id:
                if task_id in known:
                    continue
            elif known:
                # No task id to match on. scan_service commits the row before
                # it publishes and only then writes the id back, so a crash in
                # between can leave a NULL id on a row whose message IS live.
                # That message would be indistinguishable from any other id in
                # the inventory, so a NULL row is only safe to reclaim when the
                # broker is holding nothing at all.
                log.info(
                    "orphaned_queued_scan_skipped",
                    scan_id=str(scan.id),
                    reason="null_task_id_with_live_broker_messages",
                )
                continue
            scan_id: uuid.UUID = scan.id
            mark_failed(
                session,
                scan,
                "this scan was queued but the broker no longer holds a message "
                "for it (the queue was restarted or the message was lost), so "
                "no worker will ever pick it up; it is marked failed and can "
                "be retried",
            )
            orphaned.append(str(scan_id))
            log.warning(
                "orphaned_queued_scan_reaped",
                scan_id=str(scan_id),
                project_id=str(scan.project_id),
                celery_task_id=task_id,
                grace_seconds=grace_seconds,
            )
    return orphaned


@celery_app.task(name="trustedoss.stale_scan_reaper")  # type: ignore[misc]
def stale_scan_reaper_task() -> dict[str, Any]:
    """
    Mark scans failed whose worker died without marking them.

    Two passes: ``running`` rows whose worker died (``reaped``), and
    ``queued`` rows the broker is no longer holding a message for
    (``orphaned_queued``). Returns both lists plus the running pass's cutoff.
    """
    structlog.contextvars.bind_contextvars(task_name="stale_scan_reaper")
    try:
        cutoff_seconds = scan_hard_time_limit_seconds() + stale_running_scan_grace_seconds()
        cutoff = datetime.now(UTC) - timedelta(seconds=cutoff_seconds)

        reaped: list[str] = []
        with sync_session_scope() as session:
            # GREATEST over the two liveness stamps: whichever is later is the
            # last moment we can prove the task was running.
            last_alive = func.greatest(
                func.coalesce(Scan.started_at, Scan.created_at),
                Scan.updated_at,
            )
            stmt = (
                select(Scan)
                .where(Scan.status == "running")
                .where(last_alive < cutoff)
                .order_by(last_alive)
                .limit(_MAX_REAPED_PER_PASS)
            )
            stale: list[Scan] = list(session.execute(stmt).scalars().all())

            for scan in stale:
                scan_id: uuid.UUID = scan.id
                # Through mark_failed rather than a bulk UPDATE: it is the same
                # path every other failure takes, so the schedule notification
                # and the progress publish that watching clients wait on both
                # happen here too. A client parked on a killed scan otherwise
                # streams nothing forever.
                mark_failed(
                    session,
                    scan,
                    "the worker running this scan stopped without reporting a "
                    "result (killed, crashed, or restarted); no result was "
                    "recorded, so the scan is marked failed and can be retried",
                )
                reaped.append(str(scan_id))
                log.warning(
                    "stale_scan_reaped",
                    scan_id=str(scan_id),
                    project_id=str(scan.project_id),
                    cutoff_seconds=cutoff_seconds,
                )

        orphaned = _reap_orphaned_queued_scans()

        if reaped or orphaned:
            log.warning(
                "stale_scan_reaper_complete",
                reaped_count=len(reaped),
                orphaned_count=len(orphaned),
                cutoff_seconds=cutoff_seconds,
            )
        else:
            log.info("stale_scan_reaper_clean", cutoff_seconds=cutoff_seconds)
        return {
            "reaped": reaped,
            "orphaned_queued": orphaned,
            "cutoff_seconds": cutoff_seconds,
        }
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = ["stale_scan_reaper_task"]
