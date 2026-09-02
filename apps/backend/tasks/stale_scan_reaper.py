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

Only ``running`` is reaped. A ``queued`` row is waiting in the broker, where
sitting for hours is a backlog rather than a fault, and redelivery is the
broker visibility timeout's job.

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
    stale_running_scan_grace_seconds,
)
from core.db import sync_session_scope
from models import Scan
from tasks._scan_pipeline import mark_failed
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.stale_scan_reaper")

#: Cap on rows reaped per pass. A daemon restart can strand every scan that
#: was running at once, and each reap commits and fans out a notification, so
#: the pass is bounded and the next one six-hourly... in practice half-hourly,
#: picks up the rest. Without a bound one pass could hold a worker slot for a
#: long time doing notification I/O.
_MAX_REAPED_PER_PASS = 200


@celery_app.task(name="trustedoss.stale_scan_reaper")  # type: ignore[misc]
def stale_scan_reaper_task() -> dict[str, Any]:
    """
    Mark scans failed whose worker died without marking them.

    Returns ``{"reaped": [<scan_id>, ...], "cutoff_seconds": N}``.
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

        if reaped:
            log.warning(
                "stale_scan_reaper_complete",
                reaped_count=len(reaped),
                cutoff_seconds=cutoff_seconds,
            )
        else:
            log.info("stale_scan_reaper_clean", cutoff_seconds=cutoff_seconds)
        return {"reaped": reaped, "cutoff_seconds": cutoff_seconds}
    finally:
        structlog.contextvars.unbind_contextvars("task_name")


__all__ = ["stale_scan_reaper_task"]
