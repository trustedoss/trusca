"""
Admin scan-queue service — Phase 4 PR #14.

Surfaces the global scan queue to super-admin operators:

  - :func:`list_scans`   — paginated, status-filterable join of scan + project + team.
  - :func:`cancel_scan`  — Celery revoke + status='cancelled', idempotent against
                            already-terminal rows.

Cross-team visibility is intentional: super-admin sees every team's scans for
operations dashboards (queue depth, failure clusters). Team-scoped scan
listing lives in ``services.scan_service`` for non-admin callers.

Audit:
  - The cancel path mutates ``scans.status``; the SQLAlchemy ``before_flush``
    listener captures it as a ``target_table='scans', action='update'`` row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import CurrentUser
from models import Project, Scan, Team
from schemas.admin_ops import (
    AdminScanListItem,
    AdminScanListPage,
    ScanStatus,
)

log = structlog.get_logger("admin.scan.service")

# Terminal statuses where cancellation is a no-op (already done).
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AdminScanError(Exception):
    """Base class for admin scan errors mapped to RFC 7807."""

    status_code: int = 400
    title: str = "Admin Scan Error"
    type_uri: str = "about:blank"
    extensions: dict[str, object] = {}


class AdminScanNotFound(AdminScanError):
    status_code = 404
    title = "Scan Not Found"
    type_uri = "https://docs.trustedoss.io/errors/scan-not-found"
    extensions = {"scan_not_found": True}


class ScanAlreadyCancelled(AdminScanError):
    status_code = 409
    title = "Scan Already Cancelled"
    type_uri = "https://docs.trustedoss.io/errors/scan-already-cancelled"
    extensions = {"scan_already_cancelled": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _duration_seconds(scan: Scan) -> float | None:
    """Return wall-clock duration if both timestamps are set."""
    if scan.started_at is None:
        return None
    end = scan.completed_at or _now()
    return (end - scan.started_at).total_seconds()


def _build_item(
    *,
    scan: Scan,
    project_name: str,
    team_id: uuid.UUID,
    team_name: str,
) -> AdminScanListItem:
    """Materialize a Scan + project/team join row into the API shape."""
    return AdminScanListItem(
        id=scan.id,
        project_id=scan.project_id,
        project_name=project_name,
        team_id=team_id,
        team_name=team_name,
        status=scan.status,  # type: ignore[arg-type]
        kind=scan.kind,
        progress_percent=scan.progress_percent,
        started_at=scan.started_at,
        finished_at=scan.completed_at,
        duration_seconds=_duration_seconds(scan),
        error_message=scan.error_message,
        requested_by_user_id=scan.requested_by_user_id,
        created_at=scan.created_at,
    )


# ---------------------------------------------------------------------------
# list_scans
# ---------------------------------------------------------------------------


async def list_scans(
    session: AsyncSession,
    *,
    actor: CurrentUser,  # noqa: ARG001 — kept for symmetry with other admin services
    page: int = 1,
    page_size: int = 50,
    status: ScanStatus | None = None,
) -> AdminScanListPage:
    """
    Return a page of scans across every team, newest started_at first.

    The status filter is a closed Literal in the schema; non-enum values are
    rejected at the boundary as 422. ``page`` and ``page_size`` are bounded
    by the route layer (``Query(ge=..., le=...)``) but we re-clamp here so
    direct service callers cannot exceed the limits either.
    """
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)

    # JOIN scan -> project -> team in one query so we can render team /
    # project names without N+1 lookups. We keep the SELECT explicit to
    # control the columns returned (no SELECT *).
    base = (
        select(Scan, Project.name, Project.team_id, Team.name)
        .join(Project, Project.id == Scan.project_id)
        .join(Team, Team.id == Project.team_id)
    )
    count_base = (
        select(func.count())
        .select_from(Scan)
        .join(Project, Project.id == Scan.project_id)
        .join(Team, Team.id == Project.team_id)
    )

    if status is not None:
        base = base.where(Scan.status == status)
        count_base = count_base.where(Scan.status == status)

    total = int((await session.execute(count_base)).scalar_one())

    # Order: newest first by started_at when available, otherwise by created_at
    # (queued scans have no started_at). We use ``coalesce(started_at, created_at)``
    # so the queue stays time-ordered even with mixed states.
    rows_stmt = (
        base.order_by(
            func.coalesce(Scan.started_at, Scan.created_at).desc(),
            Scan.id.desc(),
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await session.execute(rows_stmt)).all()

    items = [
        _build_item(
            scan=row[0],
            project_name=row[1],
            team_id=row[2],
            team_name=row[3],
        )
        for row in rows
    ]
    return AdminScanListPage(items=items, total=total, page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# cancel_scan
# ---------------------------------------------------------------------------


async def cancel_scan(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    scan_id: uuid.UUID,
    celery_app_override: Any | None = None,
) -> AdminScanListItem:
    """
    Cancel a running / queued scan.

    Behaviour:
      - 404 when the scan does not exist (typed exception, RFC 7807 by
        the route layer).
      - 409 when the scan is already in a terminal state (succeeded /
        failed / cancelled). The extension field
        ``scan_already_cancelled = true`` distinguishes the case.
      - For queued / running scans we ``revoke(terminate=True)`` against
        the Celery task (best effort — if the task already finished
        between the SELECT and the revoke, the resulting status update
        below still wins) and stamp ``status='cancelled'``,
        ``completed_at = now()``, ``error_message = "cancelled by admin"``.

    Audit:
      - The status / completed_at mutation hits the SQLAlchemy listener
        which records a ``target_table='scans', action='update'`` row
        with the diff. No explicit AuditLog insert needed.
    """
    # Lock the row so two concurrent cancel calls cannot both pass the
    # terminal-state guard. `with_for_update` mirrors the pattern used in
    # :mod:`services.admin_user_service` for last-super-admin protection.
    stmt = select(Scan).where(Scan.id == scan_id).with_for_update()
    scan = (await session.execute(stmt)).scalar_one_or_none()
    if scan is None:
        raise AdminScanNotFound(f"scan {scan_id} not found")

    if scan.status in _TERMINAL_STATUSES:
        raise ScanAlreadyCancelled(
            f"scan {scan_id} is already in terminal state {scan.status!r}"
        )

    # Revoke the Celery task BEFORE mutating the row so that if the worker
    # is mid-flight, the SIGTERM lands while the row is still 'running'
    # (the worker's own progress hooks check for this and bail out).
    if scan.celery_task_id:
        if celery_app_override is not None:
            celery = celery_app_override
        else:
            from tasks.celery_app import celery_app

            celery = celery_app
        try:
            celery.control.revoke(scan.celery_task_id, terminate=True, signal="SIGTERM")
        except Exception as exc:  # noqa: BLE001
            # Revoke is best-effort — the broker may be unreachable, the
            # task may have finished, or the worker may be unhealthy. We
            # log and continue with the status update so the operator is
            # not blocked on transient broker hiccups.
            log.warning(
                "admin.scan.revoke_failed",
                scan_id=str(scan_id),
                celery_task_id=scan.celery_task_id,
                error=str(exc),
            )

    now = _now()
    scan.status = "cancelled"
    scan.completed_at = now
    scan.error_message = "cancelled by admin"
    scan.updated_at = now

    await session.commit()
    await session.refresh(scan)

    log.warning(
        "admin.scan.cancelled",
        actor_id=str(actor.id),
        scan_id=str(scan_id),
        celery_task_id=scan.celery_task_id,
    )

    # Re-load project + team for the response.
    project_stmt = (
        select(Project.name, Project.team_id, Team.name)
        .join(Team, Team.id == Project.team_id)
        .where(Project.id == scan.project_id)
    )
    row = (await session.execute(project_stmt)).first()
    if row is None:
        # Unreachable in practice — the FK ensures project + team exist —
        # but the service should not raise NoneType errors if the world
        # changes underfoot.
        return AdminScanListItem(
            id=scan.id,
            project_id=scan.project_id,
            project_name="",
            team_id=uuid.UUID(int=0),
            team_name="",
            status=scan.status,  # type: ignore[arg-type]
            kind=scan.kind,
            progress_percent=scan.progress_percent,
            started_at=scan.started_at,
            finished_at=scan.completed_at,
            duration_seconds=_duration_seconds(scan),
            error_message=scan.error_message,
            requested_by_user_id=scan.requested_by_user_id,
            created_at=scan.created_at,
        )
    return _build_item(
        scan=scan,
        project_name=row[0],
        team_id=row[1],
        team_name=row[2],
    )


__all__ = [
    "AdminScanError",
    "AdminScanNotFound",
    "ScanAlreadyCancelled",
    "cancel_scan",
    "list_scans",
]
