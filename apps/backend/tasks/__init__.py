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

    Raises:
        ValueError: when ``scan.kind`` is not a known scan kind. The DB
            ENUM ``scan_kind`` already restricts values, so this is a
            defensive check for typos / future kinds.
    """
    # Local imports avoid pulling Celery (and Redis) into modules that only
    # need the type hint, e.g. schemas / pure unit tests of the service layer.
    from tasks.scan_container import scan_container_task
    from tasks.scan_source import scan_source_task

    scan_id = str(scan.id)
    if scan.kind == "source":
        async_result = scan_source_task.delay(scan_id)
    elif scan.kind == "container":
        async_result = scan_container_task.delay(scan_id)
    else:
        raise ValueError(f"unknown scan.kind={scan.kind!r}")
    return str(async_result.id)


__all__ = ["enqueue_scan"]
