"""
Celery Beat task — DT health heartbeat (60s).

The actual logic lives in :mod:`integrations.dt.health` so it can be unit
tested without Celery. This module is a thin wrapper that registers the task
with the Celery app and provides a stable name (``trustedoss.dt_health``)
for the Beat schedule entry in ``tasks/celery_app.py``.
"""

from __future__ import annotations

from typing import Any

import structlog

from integrations.dt.health import run_health_check
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.dt_health")


@celery_app.task(name="trustedoss.dt_health")  # type: ignore[misc]
def dt_health_check_task() -> dict[str, Any]:
    """
    Probe Dependency-Track and update breaker state accordingly.

    Returns the heartbeat outcome as a JSON-serializable dict so Celery's
    result backend stores something useful. Beat itself ignores the return
    value; the dict is for ad-hoc debugging via ``celery inspect`` and for
    the future admin UI dashboard.
    """
    outcome = run_health_check()
    return {
        "healthy": outcome.healthy,
        "state_before": outcome.snapshot_before.state,
        "state_after": outcome.snapshot_after.state,
        "fail_count": outcome.snapshot_after.fail_count,
        "auto_restart_attempted": outcome.auto_restart_attempted,
        "error": outcome.error,
    }


__all__ = ["dt_health_check_task"]
