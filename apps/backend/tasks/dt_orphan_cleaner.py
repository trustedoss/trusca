"""
DT orphan project detector — Celery Beat (6 hours).

CLAUDE.md core rule #4 calls out that the portal must keep DT in sync. Over
time, scans that fail mid-pipeline can leave behind DT projects that no
TrustedOSS scan references — these "orphans" eat DT storage and confuse the
admin UI.

Scope of PR #8:
    Detect orphans only — write a structured log entry and return the list.
    The admin UI for review/approval lands in Phase 3.7; auto-deletion is
    explicitly off the table per the Phase 2.8 plan, so this task NEVER
    deletes anything.

Detection:
    DT projects use ``name=<project_id>`` and ``version=<scan_id>`` per the
    convention established in :func:`tasks.scan_source._run_pipeline`.
    A DT project is an orphan when its ``version`` (= scan UUID) does not
    appear in the local ``scans`` table. We do not check the name field
    because deleted projects in TrustedOSS may legitimately leave behind a
    DT version.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.db import sync_session_scope
from integrations.dt import DTError
from integrations.dt.breaker import get_breaker
from integrations.dt.client import build_client
from models import Scan
from tasks.celery_app import celery_app

log = structlog.get_logger("tasks.dt_orphan_cleaner")

_PAGE_SIZE = 100


@celery_app.task(name="trustedoss.dt_orphan_cleaner")  # type: ignore[misc]
def dt_orphan_cleaner_task() -> dict[str, Any]:
    """
    Walk DT projects, compute the set of orphans, and log the count.

    Returns ``{"checked": N, "orphans": [<dt_project_uuid>, ...]}`` so the
    admin dashboard (Phase 3.7) can poll this task's result for the latest
    snapshot. Auto-deletion is intentionally not implemented.
    """
    structlog.contextvars.bind_contextvars(task_name="dt_orphan_cleaner")
    breaker = get_breaker()
    client = build_client()
    orphans: list[str] = []
    checked = 0
    try:
        page_number = 1
        while True:
            # Inner function (not lambda) so mypy can infer the return type
            # and so the default-arg trick to bind the page number is
            # explicit rather than relying on closure semantics.
            def _fetch(pn: int = page_number) -> list[dict[str, Any]]:
                return client.list_projects(page_size=_PAGE_SIZE, page_number=pn)

            try:
                page = breaker.call(_fetch)
            except DTError as exc:
                log.warning("dt_orphan_cleaner_aborted", error=str(exc), page=page_number)
                break
            if not page:
                break
            checked += len(page)
            with sync_session_scope() as session:
                _classify_page(session, page=page, orphans=orphans)
            if len(page) < _PAGE_SIZE:
                break
            page_number += 1
    finally:
        client.close()
        structlog.contextvars.unbind_contextvars("task_name")

    if orphans:
        log.warning("dt_orphans_detected", count=len(orphans), sample=orphans[:10])
    else:
        log.info("dt_orphans_clean", checked=checked)
    return {"checked": checked, "orphans": orphans}


def _classify_page(
    session: Session, *, page: list[dict[str, Any]], orphans: list[str]
) -> None:
    """For each DT project, append its UUID to ``orphans`` if no local scan matches."""
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
            # Versions that are not UUIDs (e.g. hand-created DT projects)
            # are out of our naming convention — we can neither claim nor
            # disclaim them, so we skip rather than mark them as orphans.
            continue
        candidate_uuids.append(scan_uuid)
        by_scan_uuid[scan_uuid] = dt_uuid

    if not candidate_uuids:
        return

    found = set(
        session.execute(
            select(Scan.id).where(Scan.id.in_(candidate_uuids))
        ).scalars().all()
    )
    for scan_uuid, dt_uuid in by_scan_uuid.items():
        if scan_uuid not in found:
            orphans.append(dt_uuid)


__all__ = ["dt_orphan_cleaner_task"]
