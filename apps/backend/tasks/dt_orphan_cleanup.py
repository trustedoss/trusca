"""
DT orphan-project DELETE task — Phase 4 PR #14.

Distinct from :mod:`tasks.dt_orphan_cleaner` (the read-only Beat detector).
This task is **only** dispatched from the admin endpoint
``POST /v1/admin/dt/orphans/cleanup`` after explicit operator confirmation;
it actually deletes DT projects via ``DTClient.delete_project``.

Idempotency:
  - DT returns 404 for an unknown UUID — we catch :class:`DTClientError`
    and log "already_gone" instead of failing the whole batch. This means
    re-running the task with the same UUIDs is safe.

Concurrency:
  - The admin service holds a Redis SETNX lock (``dt:admin:orphan_cleanup_lock``)
    for the duration of the run. The task releases the lock on completion,
    success or failure. A 600-second TTL on the lock handles the worker-
    crash case automatically.

Empty-list semantics:
  - When ``dt_project_uuids`` is empty the task scans the full DT catalog
    (via the same logic as :mod:`tasks.dt_orphan_cleaner`) and deletes
    every orphan it finds. This is the "select all" path the admin UI
    surfaces with an explicit confirmation dialog.

Audit trail:
  - The Celery worker runs without a request-bound audit context, so we
    do NOT rely on the listener. Instead we explicitly insert an
    ``AuditLog`` row per deletion via the sync session — the listener
    excludes the audit table itself, so this never recurses.
"""

from __future__ import annotations

import uuid
from typing import Any

import redis
import structlog
from sqlalchemy import select

from core.config import redis_url
from core.db import sync_session_scope
from integrations.dt import DTClientError, DTError, DTUnavailable
from integrations.dt.breaker import get_breaker
from integrations.dt.client import build_client
from models import AuditLog, Scan
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.dt_orphan_cleanup")

_CLEANUP_LOCK_KEY = "dt:admin:orphan_cleanup_lock"
_DT_PAGE_SIZE = 100


@celery_app.task(  # type: ignore[misc]
    name="trustedoss.dt_orphan_cleanup",
    bind=True,
    autoretry_for=(DTUnavailable,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
def dt_orphan_cleanup_task(self: Any, dt_project_uuids: list[str]) -> dict[str, Any]:
    """
    Delete the supplied DT projects (or every detected orphan if the list is empty).

    Returns a summary dict: ``{deleted, already_gone, failed, scanned}``.
    """
    structlog.contextvars.bind_contextvars(
        task_name="dt_orphan_cleanup",
        task_id=str(self.request.id) if self and self.request else None,
    )
    breaker = get_breaker()
    client = build_client()
    rds = redis.Redis.from_url(redis_url(), decode_responses=True)

    deleted: list[str] = []
    already_gone: list[str] = []
    failed: list[dict[str, str]] = []
    scanned = 0

    try:
        if dt_project_uuids:
            targets = list(dt_project_uuids)
        else:
            # Empty list = "find every orphan and delete it". We page through
            # DT, classify orphans, and add them to the target list inline.
            targets = []
            page_number = 1
            while True:
                current_page = page_number

                def _fetch(pn: int = current_page) -> list[dict[str, Any]]:
                    return client.list_projects(page_size=_DT_PAGE_SIZE, page_number=pn)

                try:
                    page = breaker.call(_fetch)
                except DTError as exc:
                    log.error("orphan_cleanup_list_failed", error=str(exc), page=page_number)
                    raise
                if not page:
                    break
                scanned += len(page)
                with sync_session_scope() as session:
                    _classify_into(session, page=page, sink=targets)
                if len(page) < _DT_PAGE_SIZE:
                    break
                page_number += 1

        for raw_uuid in targets:
            # Validate UUID shape defensively — even though the schema layer
            # already parsed list inputs, the empty-list branch above
            # collects strings from DT itself which is technically untrusted.
            try:
                normalized = str(uuid.UUID(str(raw_uuid)))
            except (ValueError, TypeError):
                failed.append({"uuid": str(raw_uuid), "error": "invalid uuid"})
                continue

            try:

                def _delete(target: str = normalized) -> None:
                    client.delete_project(project_uuid=target)

                breaker.call(_delete)
                deleted.append(normalized)
                _emit_audit(target_uuid=normalized, action="delete")
                log.warning("orphan_deleted", dt_project_uuid=normalized)
            except DTClientError as exc:
                # 4xx — most often 404 (project already deleted). Treat as
                # idempotent success but record separately for the response.
                already_gone.append(normalized)
                _emit_audit(
                    target_uuid=normalized,
                    action="delete_skipped_missing",
                )
                log.info("orphan_already_gone", dt_project_uuid=normalized, error=str(exc))
            except DTUnavailable:
                # 5xx / network — propagate so Celery autoretry kicks in.
                # We do NOT mark the lock released here because autoretry
                # re-runs the task body which will re-enter this branch.
                raise
            except Exception as exc:  # noqa: BLE001 — final guard
                failed.append({"uuid": normalized, "error": str(exc)})
                log.error("orphan_delete_failed", dt_project_uuid=normalized, error=str(exc))

    finally:
        client.close()
        # G2 (CWE-362): release the lock only on terminal completion, not on
        # Celery autoretry.  self.retry() raises celery.exceptions.Retry which
        # propagates through this finally block; if we deleted the lock here, a
        # second concurrent invocation could start before the retry fires,
        # creating a race window.  We detect the in-flight Retry and skip the
        # delete so the lock stays held for the duration of the logical run.
        import sys  # noqa: PLC0415 — local import to avoid circular at module level

        from celery.exceptions import Retry  # noqa: PLC0415

        active_exc = sys.exc_info()[1]
        if not isinstance(active_exc, Retry):
            try:
                rds.delete(_CLEANUP_LOCK_KEY)
            except redis.RedisError as lock_exc:
                log.warning("orphan_cleanup_lock_release_failed", error=str(lock_exc))
        structlog.contextvars.unbind_contextvars("task_name", "task_id")

    summary = {
        "deleted": deleted,
        "already_gone": already_gone,
        "failed": failed,
        "scanned": scanned,
    }
    log.warning(
        "orphan_cleanup_complete",
        deleted_count=len(deleted),
        already_gone_count=len(already_gone),
        failed_count=len(failed),
        scanned=scanned,
    )
    return summary


def _classify_into(
    session: Any,
    *,
    page: list[dict[str, Any]],
    sink: list[str],
) -> None:
    """Append each orphan UUID found in ``page`` to ``sink``.

    Mirrors :func:`tasks.dt_orphan_cleaner._classify_page` but produces UUID
    strings instead of DT-uuid strings (kept simple — same conversion).
    """
    candidate_uuids: list[uuid.UUID] = []
    by_scan_uuid: dict[uuid.UUID, str] = {}
    for project in page:
        if not isinstance(project, dict):
            continue
        version = project.get("version")
        dt_uuid = project.get("uuid")
        if not isinstance(version, str) or not isinstance(dt_uuid, str):
            continue
        try:
            scan_uuid = uuid.UUID(version)
        except ValueError:
            continue
        candidate_uuids.append(scan_uuid)
        by_scan_uuid[scan_uuid] = dt_uuid

    if not candidate_uuids:
        return

    found = set(
        session.execute(select(Scan.id).where(Scan.id.in_(candidate_uuids))).scalars().all()
    )
    for scan_uuid, dt_uuid in by_scan_uuid.items():
        if scan_uuid not in found:
            sink.append(dt_uuid)


def _emit_audit(*, target_uuid: str, action: str) -> None:
    """
    Insert an ``AuditLog`` row for the deletion outcome.

    Celery tasks run outside the request lifecycle so the contextvar-based
    listener has no actor to attach. We deliberately leave ``actor_user_id``
    NULL and tag ``target_table='dt_projects'`` — the schema's enum check
    is at the domain-table level (``audit_logs.target_table`` is just a
    String), so an out-of-band table name is allowed.
    """
    with sync_session_scope() as session:
        row = AuditLog(
            actor_user_id=None,
            team_id=None,
            target_table="dt_projects",
            target_id=target_uuid,
            action=action,
            request_id=None,
            ip=None,
            user_agent="celery/trustedoss.dt_orphan_cleanup",
            diff={"dt_project_uuid": target_uuid},
        )
        session.add(row)
        session.commit()


__all__ = ["dt_orphan_cleanup_task", "redis"]
