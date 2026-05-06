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
    "tasks.dt_health",
]


def _build_beat_schedule() -> dict[str, dict[str, object]]:
    """
    Return the Celery Beat schedule.

    Phase 2 PR #8 registers four periodic tasks:
      - ``trustedoss.dt_health``           — every 60 seconds
      - ``trustedoss.dt_resync``           — every 1 hour
      - ``trustedoss.dt_orphan_cleaner``   — every 6 hours

    The actual Beat sidecar container lands in a follow-up PR; for #8 we
    register the schedule so a Beat process started against this app picks
    them up automatically.
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
