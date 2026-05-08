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
from core.pii_mask import mask_pii
from core.security import CurrentUser
from models import Project, Scan
from schemas.scan import ScanCreate
from tasks import enqueue_scan

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


class ScanEnqueueFailed(ScanError):
    """The Celery dispatcher rejected the scan (broker down, bad kind, etc.).

    The Scan row has been written and then transitioned to ``status='failed'``
    with ``error_message='enqueue_failed: ...'``. The router maps this to
    503 Service Unavailable so caller automation knows it is safe to retry.
    """

    status_code = 503
    title = "Scan Enqueue Failed"


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

    PR #8 wiring (this PR):
      1. Persist the ``scans`` row with ``status='queued'``.
      2. Update ``project.latest_scan_id`` so list pages reflect the most
         recent scan even while it is still queued.
      3. Call ``enqueue_scan(scan)`` (the Celery dispatcher in
         ``tasks/__init__.py``) and store the returned task id back on the
         row. If the dispatcher raises (broker down, unknown kind), we mark
         the scan ``failed`` with ``error_message='enqueue_failed: ...'``
         and raise :class:`ScanEnqueueFailed` (503).

    Concurrency: ``ix_scans_project_active`` (UNIQUE on project_id WHERE
    status IN ('queued','running')) makes step 1 atomic — a second
    concurrent caller hits :class:`ScanInProgressConflict` (409) without
    ever reaching the Celery dispatcher.
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

    # Defence in depth: even though `ScanCreate._validate_metadata` already
    # bounds size + depth, we mask any nested credential-shaped keys so the
    # audit listener (core.audit) cannot accidentally persist a secret into
    # the audit log diff JSONB. The mask returns a fresh deep copy.
    safe_metadata = mask_pii(dict(payload.metadata))

    scan = Scan(
        project_id=project_id_value,
        kind=payload.kind,
        status="queued",
        progress_percent=0,
        current_step=None,
        celery_task_id=None,  # set below after enqueue_scan(...)
        requested_by_user_id=actor.id,
        scan_metadata=safe_metadata,
    )
    session.add(scan)
    # Flush so `scan.id` is populated; we need it to update
    # `project.latest_scan_id` in the same transaction.
    try:
        await session.flush()
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

    # I-2: keep the project.latest_scan_id pointer in sync so list pages
    # (which load `latest_scan_id` denormalized to avoid a per-row JOIN) can
    # show "in progress" badges immediately after queueing. The same FK is
    # NOT touched on terminal status transitions — the latest scan is
    # whichever was most recently triggered, regardless of outcome.
    project.latest_scan_id = scan.id

    try:
        await session.commit()
    except IntegrityError as exc:
        # A second caller racing on the partial unique index might still
        # produce IntegrityError at commit time (the flush above is the
        # primary check, but commit-time constraint validation is also
        # possible if the txn was held briefly). Translate identically.
        await session.rollback()
        raise ScanInProgressConflict(
            f"a scan is already queued or running for project {project_id_value}",
        ) from exc

    await session.refresh(scan)

    # ------------------------------------------------------------------
    # Celery dispatch. Sync call (Celery's .delay() is sync) — no `await`.
    # ------------------------------------------------------------------
    try:
        celery_task_id = enqueue_scan(scan)
    except Exception as exc:
        # The scan row exists in 'queued' state but no worker will ever pick
        # it up. Flip it to 'failed' with a deterministic prefix so callers
        # can distinguish enqueue failures from pipeline failures.
        log.error(
            "scan_enqueue_failed",
            scan_id=str(scan.id),
            project_id=str(project_id_value),
            error=str(exc),
            exc_info=True,
        )
        scan.status = "failed"
        scan.error_message = f"enqueue_failed: {exc}"
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            # Failure-to-mark-failed should not mask the original cause.
            await session.rollback()
        raise ScanEnqueueFailed(
            f"failed to enqueue scan for project {project_id_value}: {exc}",
        ) from exc

    scan.celery_task_id = celery_task_id
    await session.commit()
    await session.refresh(scan)

    log.info(
        "scan_queued",
        scan_id=str(scan.id),
        project_id=str(project_id_value),
        team_id=str(project_team_id),
        kind=scan.kind,
        celery_task_id=celery_task_id,
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


# ---------------------------------------------------------------------------
# Cross-project list — Step 4 (Phase 3 wrap-up)
# ---------------------------------------------------------------------------


async def list_scans_for_actor(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    status_filter: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[Scan], int]:
    """
    Return (scans, total) across every project the *actor* can see.

    Scope:
      - super_admin: all scans, regardless of team.
      - everyone else: scans whose project's team is in ``actor.team_ids``.
        An actor with no team memberships sees an empty page (not 403); the
        endpoint is read-only and "I am authenticated but my account has no
        teams yet" is a legitimate visible state for the SPA.

    ``status_filter`` is an optional value from ``SCAN_STATUS_VALUES``
    (queued/running/succeeded/failed/cancelled). Validation lives in the
    router (Pydantic regex constraint); we trust it here. Anything else is
    silently ignored — defense in depth without 422 churn.
    """
    page = max(page, 1)
    size = max(min(size, 100), 1)

    is_super = actor.is_superuser or actor.role == "super_admin"

    # Build the base query. We JOIN on Project so the WHERE clause can clamp
    # by team_id. ix_scans_project_created_at + ix_projects_team_id keep the
    # plan cheap for typical actor team-list sizes (≤ 50 teams).
    base = select(Scan).join(Project, Project.id == Scan.project_id)
    count_base = select(func.count()).select_from(Scan).join(
        Project, Project.id == Scan.project_id
    )

    if not is_super:
        team_ids = list(actor.team_ids)
        if not team_ids:
            return [], 0
        base = base.where(Project.team_id.in_(team_ids))
        count_base = count_base.where(Project.team_id.in_(team_ids))

    if status_filter is not None:
        base = base.where(Scan.status == status_filter)
        count_base = count_base.where(Scan.status == status_filter)

    total_result = await session.execute(count_base)
    total = int(total_result.scalar_one())

    # Order by created_at DESC (most recent first). Tie-break on id so
    # pagination is stable when two scans share a microsecond.
    rows_stmt = (
        base.order_by(Scan.created_at.desc(), Scan.id.desc())
        .limit(size)
        .offset((page - 1) * size)
    )
    rows_result = await session.execute(rows_stmt)
    rows = list(rows_result.scalars().all())
    return rows, total


__all__ = [
    "ProjectMissingForScan",
    "ScanEnqueueFailed",
    "ScanError",
    "ScanForbidden",
    "ScanInProgressConflict",
    "ScanNotFound",
    "get_scan",
    "list_scans_for_actor",
    "list_scans_for_project",
    "trigger_scan",
]
