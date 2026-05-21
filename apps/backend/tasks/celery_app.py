"""
Celery application bootstrap.

Phase 0 PR #2 stood up the worker process. Phase 2 PR #8 registers the real
scan tasks (``scan_source``, ``scan_container``), the DT health/resync/orphan
tasks, and the corresponding Beat schedule.

CLAUDE.md core rule #11: environment variables are read inside the factory at
process startup, not cached as module-level constants. The Beat schedule
itself is built from constants — changing the cadence requires a code change,
which is the right granularity for cron-shaped configuration.

Task module loading:
    Celery autodiscovers task modules listed in ``include=``. We list each
    Phase 2 task module so the worker registers them on boot. Importing here
    ensures the task names are bound to ``celery_app`` (not a different
    Celery() instance constructed elsewhere).
"""

from __future__ import annotations

from datetime import timedelta

from celery import Celery
from celery.schedules import crontab
from celery.schedules import schedule as _schedule

from core.config import redis_url
from core.logging import configure_logging

# Tasks defined in this PR — listed by import path so Celery can autoload
# them. Beat schedule entries below reference these by their ``name=`` kwargs.
_TASK_INCLUDES = [
    "tasks.scan_source",
    "tasks.scan_container",
    "tasks.dt_resync",
    "tasks.dt_orphan_cleaner",
    "tasks.dt_orphan_cleanup",
    "tasks.dt_health",
    # feat/zip-upload (security H-fix) — stale uploaded-archive retention sweep.
    "tasks.source_archive_cleaner",
    # Phase 6 PR #18 — multi-channel notification fan-out (email/Slack/Teams).
    "tasks.notify",
    # Phase 6 chore PR #19 — automated backup + restore tasks.
    "tasks.backup",
    # PR-A1 (scan stability) — reclaim workspaces left by cancelled / killed /
    # crashed scans whose `finally: rmtree` did not run.
    "tasks.workspace_cleaner",
]


def _build_beat_schedule() -> dict[str, dict[str, object]]:
    """
    Return the Celery Beat schedule.

    Phase 2 PR #8 registers three periodic tasks:
      - ``trustedoss.dt_health``           — every 60 seconds
      - ``trustedoss.dt_resync``           — every 1 hour
      - ``trustedoss.dt_orphan_cleaner``   — every 6 hours

    chore PR #4 wires a `celery-beat` sidecar in
    ``docker-compose.dev.yml`` so these schedules actually fire — until
    that PR landed the schedule was registered but no process was
    invoking it.
    """
    return {
        "dt-health-heartbeat": {
            "task": "trustedoss.dt_health",
            "schedule": _schedule(timedelta(seconds=60)),
        },
        "dt-resync-hourly": {
            "task": "trustedoss.dt_resync",
            "schedule": _schedule(timedelta(hours=1)),
        },
        "dt-orphan-cleaner-six-hourly": {
            "task": "trustedoss.dt_orphan_cleaner",
            "schedule": _schedule(timedelta(hours=6)),
        },
        # feat/zip-upload (security H-fix) — reclaim abandoned / orphaned
        # uploaded archives every 6h so a looped-upload DoS or a
        # SIGKILL-before-extract leak cannot fill the workspace volume.
        "source-archive-cleaner-six-hourly": {
            "task": "trustedoss.source_archive_cleaner",
            "schedule": _schedule(timedelta(hours=6)),
        },
        # PR-A1 (scan stability) — reclaim orphaned scan workspaces every
        # 30 minutes. Cheap (one stat() per dir + a single bounded SELECT),
        # frequent enough that a SIGKILL/cancel orphan never lingers long
        # enough to threaten the disk hard limit.
        "workspace-cleaner-half-hourly": {
            "task": "trustedoss.workspace_cleaner",
            "schedule": _schedule(timedelta(minutes=30)),
        },
        # Phase 6 chore PR #19 — daily auto-backup at 00:00 UTC. The task
        # itself applies a 7-day retention pass to ``auto-*`` backups after
        # writing the new artifact; manual backups are never auto-pruned.
        "daily-auto-backup": {
            "task": "trustedoss.backup.run",
            "schedule": crontab(hour=0, minute=0),
            "kwargs": {"kind": "auto", "actor_user_id": None},
        },
    }


def create_celery_app() -> Celery:
    broker = redis_url()
    app = Celery(
        "trustedoss",
        broker=broker,
        backend=broker,
        include=list(_TASK_INCLUDES),
    )
    app.conf.update(
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        # PR-A1 (scan stability): do NOT set a GLOBAL task time limit here.
        # A global ``task_soft_time_limit`` / ``task_time_limit`` would also
        # cap notification / backup / DT tasks, which is wrong — a 1-hour
        # ceiling on a Slack webhook is meaningless and a backup of a large
        # DB can legitimately run longer than a scan. Scan tasks instead
        # receive their limits per-dispatch in ``tasks.enqueue_scan`` (read
        # from env at call time per CLAUDE.md rule #11) so only the two scan
        # tasks are time-boxed.
        task_default_queue="trustedoss.default",
        timezone="UTC",
        enable_utc=True,
        beat_schedule=_build_beat_schedule(),
        # Use JSON serialization end-to-end. Pickle is the Celery default but
        # opens an RCE surface if the broker is ever exposed; JSON forces
        # task arguments to be plain strings/ints (we pass UUIDs as str).
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )
    configure_logging()
    return app


celery_app = create_celery_app()
