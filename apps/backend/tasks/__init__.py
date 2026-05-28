"""
Celery task package.

Phase 2 PR #8 introduces the real scan pipeline. The dispatcher
:func:`enqueue_scan` is the single entry point that the FastAPI service layer
(``services/scan_service.py::trigger_scan``) calls after persisting a queued
``Scan`` row — keeping the ``.delay()`` call out of the service file means
backend-developer can write the API code without importing Celery, and the
test harness can monkey-patch one symbol to short-circuit the pipeline.

The dispatcher branches on ``scan.kind``:
    - ``"source"``    → :func:`tasks.scan_source.scan_source_task`
    - ``"container"`` → :func:`tasks.scan_container.scan_container_task`

Both tasks accept ``scan_id`` as a UUID string (Celery serialization is JSON;
see ``tasks/celery_app.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Importing the model here would create a heavy import-time chain
    # (models → SQLAlchemy → asyncpg) for any consumer of `tasks/`. The
    # dispatcher only reads ``scan.id`` and ``scan.kind`` so a structural
    # type via ``TYPE_CHECKING`` keeps runtime imports minimal. With
    # ``from __future__ import annotations`` the forward reference is a
    # plain string at runtime — no quotes needed in the annotation itself.
    from models import Scan


def enqueue_scan(scan: Scan) -> str:
    """
    Dispatch the appropriate scan task for `scan` and return the Celery task id.

    The caller is expected to set ``scan.celery_task_id`` to the returned
    value and commit. We deliberately do NOT mutate the ORM row here so the
    service layer remains in control of the transaction boundary.

    PR-A1 (scan stability): the two scan tasks are time-boxed so a hung
    cdxgen / scancode / Trivy step cannot pin a worker slot forever. We pass
    ``soft_time_limit`` + ``time_limit`` per dispatch via ``apply_async`` —
    NOT as a global Celery conf or import-time decorator constant — so:

      1. Only scan tasks get the limit (notifications / backups / DT tasks
         are unaffected — see ``tasks.celery_app``).
      2. The env vars are read at call time (CLAUDE.md core rule #11): an
         operator retunes ``SCAN_SOFT_TIME_LIMIT_SECONDS`` /
         ``SCAN_HARD_TIME_LIMIT_SECONDS`` and the next dispatch picks it up
         without a worker rebuild.

    The soft limit is the primary mechanism: it raises
    :class:`celery.exceptions.SoftTimeLimitExceeded` inside the task so it can
    clean up the workspace and mark the scan ``failed``. The hard limit is the
    SIGKILL safety net for a task that ignores the soft signal.

    Raises:
        ValueError: when ``scan.kind`` is not a known scan kind. The DB
            ENUM ``scan_kind`` already restricts values, so this is a
            defensive check for typos / future kinds.
    """
    # Local imports avoid pulling Celery (and Redis) into modules that only
    # need the type hint, e.g. schemas / pure unit tests of the service layer.
    from core.config import (
        scan_hard_time_limit_seconds,
        scan_soft_time_limit_seconds,
    )
    from tasks.scan_container import scan_container_task
    from tasks.scan_source import scan_source_task

    scan_id = str(scan.id)
    # Read both env-driven limits at dispatch time (rule #11).
    soft_limit = scan_soft_time_limit_seconds()
    hard_limit = scan_hard_time_limit_seconds()
    if scan.kind == "source":
        async_result = scan_source_task.apply_async(
            args=(scan_id,),
            soft_time_limit=soft_limit,
            time_limit=hard_limit,
        )
    elif scan.kind == "container":
        async_result = scan_container_task.apply_async(
            args=(scan_id,),
            soft_time_limit=soft_limit,
            time_limit=hard_limit,
        )
    else:
        raise ValueError(f"unknown scan.kind={scan.kind!r}")
    return str(async_result.id)


def enqueue_reachability(scan_id: str) -> str | None:
    """Dispatch the v2.3 r1 reachability enrichment for a succeeded source scan.

    Called by ``tasks.scan_source`` at the end of a successful source scan to run
    Go ``govulncheck`` over the preserved source and stamp a reachability signal
    onto the scan's findings. It is a SEPARATE task (not part of the scan
    pipeline) so the user-facing scan completes and reports ``succeeded``
    immediately — reachability arrives asynchronously after.

    Returns the Celery task id, or ``None`` when reachability is disabled via
    ``REACHABILITY_ENABLED=false`` (read at call time per CLAUDE.md rule #11).
    Best-effort: a dispatch failure NEVER propagates into the calling scan — the
    caller wraps this in its own swallow-and-log guard.

    Args:
        scan_id: the source scan's UUID **string** (Celery JSON serialization).
    """
    from core.config import reachability_enabled

    if not reachability_enabled():
        return None
    # Local import (mirrors enqueue_scan) so modules that only type-check
    # ``Scan`` never pull Celery / Redis at import time.
    from tasks.scan_reachability import scan_reachability_task

    async_result = scan_reachability_task.apply_async(args=(scan_id,))
    return str(async_result.id)


__all__ = ["enqueue_reachability", "enqueue_scan"]
