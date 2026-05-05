"""
Scan domain services — Phase 2 PR #7 (skeleton).

PR #7 only persists the `scans` row with status='queued' and `celery_task_id
= None`. The Celery `.delay(...)` call that turns the queued row into a
running pipeline lands in PR #8 (scan-pipeline-specialist) — the comment
inside `trigger_scan` flags the exact insertion point.

Concurrency contract (CLAUDE.md core rule #3 + models/scan.py partial unique
index `ix_scans_project_active`): at most one scan per project may be in
state queued|running. The DB rejects a second INSERT with IntegrityError; we
translate that to `ScanInProgressConflict` (409) so callers get a stable RFC
7807 envelope instead of a Python traceback.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import audit_context
from core.security import CurrentUser
from models import Project, Scan
from schemas.scan import ScanCreate

log = structlog.get_logger("scan.service")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class ScanError(Exception):
    """Base class for scan-domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Scan Error"


class ScanNotFound(ScanError):
    status_code = 404
    title = "Scan Not Found"


class ScanForbidden(ScanError):
    status_code = 403
    title = "Forbidden"


class ScanInProgressConflict(ScanError):
    status_code = 409
    title = "Scan Already In Progress"


class ProjectMissingForScan(ScanError):
    """The project referenced by a scan trigger no longer exists."""

    status_code = 404
    title = "Project Not Found"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bind_audit_team(team_id: uuid.UUID) -> None:
    ctx = dict(audit_context.get() or {})
    ctx["team_id"] = str(team_id)
    audit_context.set(ctx)


def _can_access_team(actor: CurrentUser, team_id: uuid.UUID) -> bool:
    if actor.is_superuser or actor.role == "super_admin":
        return True
    return team_id in actor.team_ids


async def _load_project(session: AsyncSession, project_id: uuid.UUID) -> Project:
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise ProjectMissingForScan(f"project {project_id} not found")
    return project


# ---------------------------------------------------------------------------
# Trigger scan (skeleton — Celery enqueue lands in PR #8)
# ---------------------------------------------------------------------------


async def trigger_scan(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    payload: ScanCreate,
    actor: CurrentUser,
) -> Scan:
    """
    Insert a queued scan row for `project_id`.

    Returns the new Scan ORM row. The router converts it to ScanPublic.

    Concurrency: relies on the partial unique index `ix_scans_project_active`
    (UNIQUE on project_id WHERE status IN ('queued','running')). When a
    second scan is triggered while one is still queued or running, Postgres
    raises IntegrityError and we translate to ScanInProgressConflict.

    PR #8 hand-off: after the row is committed and we know its id, enqueue
    the Celery task::

        from tasks.scan import run_scan_pipeline
        result = run_scan_pipeline.delay(scan_id=str(scan.id))
        scan.celery_task_id = result.id
        await session.commit()

    For PR #7 we leave celery_task_id=None to keep the surface free of any
    Celery import or broker dependency. Tests can drive scans directly via
    the service.
    """
    project = await _load_project(session, project_id)
    if not _can_access_team(actor, project.team_id):
        raise ScanForbidden(
            f"actor is not a member of team {project.team_id}",
        )

    _bind_audit_team(project.team_id)

    # Capture identifiers BEFORE the commit. After session.rollback() the
    # Project ORM row's attributes are expired; touching them in the except
    # branch would trigger a sync lazy-load on an async engine and raise
    # MissingGreenlet. Plain locals are safe.
    project_id_value = project.id
    project_team_id = project.team_id

    scan = Scan(
        project_id=project_id_value,
        kind=payload.kind,
        status="queued",
        progress_percent=0,
        current_step=None,
        celery_task_id=None,  # PR #8 will set this after .delay(...)
        requested_by_user_id=actor.id,
        scan_metadata=dict(payload.metadata),
    )
    session.add(scan)
    try:
        await session.commit()
    except IntegrityError as exc:
        # The partial unique index on (project_id) WHERE status IN
        # ('queued','running') is the canonical signal. Postgres returns the
        # constraint name in the orig message; we don't switch on it because
        # the only realistic constraint that fires from this INSERT is the
        # active-scan one — projects are validated above and the FK target
        # exists.
        await session.rollback()
        raise ScanInProgressConflict(
            f"a scan is already queued or running for project {project_id_value}",
        ) from exc

    await session.refresh(scan)
    log.info(
        "scan_queued",
        scan_id=str(scan.id),
        project_id=str(project_id_value),
        team_id=str(project_team_id),
        kind=scan.kind,
    )
    return scan


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_scan(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    actor: CurrentUser,
) -> Scan:
    """Return the scan, raising 404 / 403 as appropriate."""
    result = await session.execute(select(Scan).where(Scan.id == scan_id))
    scan = result.scalar_one_or_none()
    if scan is None:
        raise ScanNotFound(f"scan {scan_id} not found")

    project = await _load_project(session, scan.project_id)
    if not _can_access_team(actor, project.team_id):
        raise ScanForbidden(
            f"actor is not a member of team {project.team_id}",
        )
    return scan


async def list_scans_for_project(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    actor: CurrentUser,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Scan], int]:
    """Return (scans, total) ordered by created_at desc, paginated."""
    page = max(page, 1)
    size = max(min(size, 100), 1)

    project = await _load_project(session, project_id)
    if not _can_access_team(actor, project.team_id):
        raise ScanForbidden(
            f"actor is not a member of team {project.team_id}",
        )

    total_result = await session.execute(
        select(func.count()).select_from(Scan).where(Scan.project_id == project_id)
    )
    total = int(total_result.scalar_one())

    rows_stmt = (
        select(Scan)
        .where(Scan.project_id == project_id)
        # ix_scans_project_created_at supports this ordering directly.
        .order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    rows_result = await session.execute(rows_stmt)
    rows = list(rows_result.scalars().all())
    return rows, total


__all__ = [
    "ProjectMissingForScan",
    "ScanError",
    "ScanForbidden",
    "ScanInProgressConflict",
    "ScanNotFound",
    "get_scan",
    "list_scans_for_project",
    "trigger_scan",
]
