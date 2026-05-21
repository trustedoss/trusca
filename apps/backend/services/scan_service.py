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

import os
import uuid

import structlog
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import bind_audit_team as _bind_audit_team
from core.pii_mask import mask_pii
from core.security import CurrentUser
from models import Project, Scan
from schemas.scan import ScanCreate
from services.source_archive_service import (
    SourceArchiveError,
    resolve_existing_archive,
)
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


class ConcurrentScanLimitExceeded(ScanError):
    """The triggering team already has the max number of active scans.

    B1: a per-team stability cap on concurrent (queued+running) scans,
    independent of the per-project active-scan unique index. Protects the
    shared Celery worker pool from a single team's burst when hundreds of
    users are online. Mapped to 429 Too Many Requests with a ``Retry-After``
    header and the RFC 7807 extension field ``limit`` so callers (and CI
    automation) can back off intelligently.

    M1 (security-reviewer): the live ``running_scans`` count is carried on the
    exception instance for server-side logging only — it is deliberately NOT
    exposed in the response body. Returning the team's real-time active-scan
    count to every team developer is an intra-team side-channel (it leaks how
    busy teammates are / how close the team is to its cap on each individual
    request). ``limit`` + ``Retry-After`` are sufficient for a client to back
    off; the precise count adds no client value over those two.
    """

    status_code = 429
    title = "Concurrent Scan Limit Exceeded"
    type_uri = "urn:trustedoss:problem:concurrent_scan_limit"
    # Seconds the client should wait before retrying; scans are long-running
    # so a coarse 30s back-off is appropriate (a finished scan frees a slot).
    retry_after_seconds = 30

    def __init__(self, message: str, *, running_scans: int, limit: int) -> None:
        super().__init__(message)
        # Server-side only (log context). Not serialized into the 429 body.
        self.running_scans = running_scans
        self.limit = limit


class ScanEnqueueFailed(ScanError):
    """The Celery dispatcher rejected the scan (broker down, bad kind, etc.).

    The Scan row has been written and then transitioned to ``status='failed'``
    with ``error_message='enqueue_failed: ...'``. The router maps this to
    503 Service Unavailable so caller automation knows it is safe to retry.
    """

    status_code = 503
    title = "Scan Enqueue Failed"


class ScanDiskFull(ScanError):
    """The host workspace volume is over the hard limit (DISK_HARD_LIMIT_PCT).

    Mapped to 503 Service Unavailable so CI integrations know to retry later.
    """

    status_code = 503
    title = "Workspace Disk Full"


class ProjectMissingForScan(ScanError):
    """The project referenced by a scan trigger no longer exists."""

    status_code = 404
    title = "Project Not Found"


class ScanArchiveMissing(ScanError):
    """An upload-source scan referenced an archive_id with no file on disk.

    Maps to 404 so CI / UI callers learn the zip must be (re-)uploaded via
    POST /v1/projects/{id}/source-archive before retriggering the scan.
    """

    status_code = 404
    title = "Source Archive Not Found"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _concurrency_cap_per_team() -> int:
    """Per-team active-scan cap. Read at call time (CLAUDE.md core rule #11)."""
    from core.config import scan_concurrency_cap_per_team

    return scan_concurrency_cap_per_team()


async def _count_active_scans_for_team(
    session: AsyncSession, team_id: uuid.UUID
) -> int:
    """Count scans in state queued|running across all of the team's projects.

    B1: the per-team concurrency cap. We JOIN scans -> projects and clamp by
    Project.team_id so the count covers every project the team owns, not just
    the one being triggered. ``ix_scans_project_active`` (partial index on the
    active states) keeps the predicate cheap; ``ix_projects_team_id`` covers
    the team clamp.
    """
    stmt = (
        select(func.count())
        .select_from(Scan)
        .join(Project, Project.id == Scan.project_id)
        .where(Project.team_id == team_id)
        .where(Scan.status.in_(("queued", "running")))
    )
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def _enforce_team_concurrency_cap(
    session: AsyncSession, team_id: uuid.UUID
) -> None:
    """Raise :class:`ConcurrentScanLimitExceeded` if the team is at the cap.

    A cap of 0 (or negative) disables the check entirely — the operator has
    opted out and only the per-project unique index + per-user rate limit
    apply.

    Note (race window — soft cap): this SELECT-then-INSERT is not atomic
    across concurrent triggers from the same team. N requests can each read
    ``active == cap - 1`` before any of them INSERTs, and all N proceed,
    overshooting the cap.

    M2 (security-reviewer): worst-case bound. The overshoot is bounded, not
    unbounded, by two independent controls:

      * the per-project unique partial index (``ix_scans_project_active``)
        guarantees at most ONE active scan per project, so a single project
        can never contribute more than 1 to the overshoot; and
      * the per-user scan-trigger rate limit (``SCAN_TRIGGER_RATE_LIMIT``,
        default 20/min) bounds how many triggers any one member can fire in
        the race window.

    So with ``cap`` and ``n_members`` members each able to fire at their
    per-user rate limit ``rate_limit``, the active-scan count for a team is
    bounded by::

        cap + (rate_limit * n_members) - 1

    i.e. a brief, bounded burst rather than a runaway. That is acceptable for
    a *stability* guard — the worker pool tolerates a transient overshoot, and
    finished scans free slots within minutes. We deliberately do NOT take a
    team-level advisory lock (``pg_advisory_xact_lock``): it would add a
    round-trip on the hot trigger path for a guard whose only failure mode is
    a short, bounded overshoot. The boundary + the bounded-race behaviour are
    pinned by the unit tests (incl. the high fan-out race) so a future
    tightening is a conscious change.
    """
    cap = _concurrency_cap_per_team()
    if cap <= 0:
        return
    active = await _count_active_scans_for_team(session, team_id)
    if active >= cap:
        log.warning(
            "scan.concurrency_cap_blocked",
            team_id=str(team_id),
            active_scans=active,
            limit=cap,
        )
        raise ConcurrentScanLimitExceeded(
            f"team {team_id} has {active} active scans (limit {cap})",
            running_scans=active,
            limit=cap,
        )


def _disk_hard_limit_pct() -> float:
    """Hard cutoff for workspace disk usage. Above this, new scans 503.

    Default 95% — matches the admin disk warning thresholds (80 warn / 90
    critical) but stays below 100 so the operator has room to clean up
    before any one scan exceeds total capacity.
    """
    return float(os.getenv("DISK_HARD_LIMIT_PCT", "95.0"))


def _check_disk_guard() -> None:
    """Raise :class:`ScanDiskFull` if workspace volume is past the hard limit.

    Best-effort: if statvfs fails (eg. workspace dir missing), we let the
    scan through — the alternative (blanket 503) is worse than a scan that
    hits a real disk error inside the worker. Operators get the warning
    via the admin disk dashboard either way.
    """
    workspace = os.getenv("WORKSPACE_HOST_PATH", "/opt/trustedoss/workspace")
    try:
        stat = os.statvfs(workspace)
    except OSError as exc:
        log.warning(
            "scan.disk_guard_unavailable",
            workspace=workspace,
            error=type(exc).__name__,
        )
        return
    total = stat.f_blocks * stat.f_frsize
    free = stat.f_bavail * stat.f_frsize
    if total <= 0:
        return
    used_pct = ((total - free) / total) * 100.0
    limit = _disk_hard_limit_pct()
    if used_pct >= limit:
        log.error(
            "scan.disk_guard_blocked",
            workspace=workspace,
            used_pct=round(used_pct, 1),
            limit_pct=limit,
        )
        raise ScanDiskFull(
            f"workspace disk usage {used_pct:.1f}% >= hard limit {limit:.1f}%"
        )


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

    # B1 — per-team concurrency cap. Reject the trigger up front when the
    # team already has the maximum number of queued+running scans, protecting
    # the shared Celery worker pool from a single team's burst. This is a
    # soft stability cap (see _enforce_team_concurrency_cap docstring on the
    # SELECT-then-INSERT race), distinct from the hard per-project unique
    # index enforced below at flush time.
    await _enforce_team_concurrency_cap(session, project.team_id)

    # feat/zip-upload: when the scan asks for an uploaded source archive,
    # verify the file exists on disk *before* we enqueue. Otherwise the worker
    # would dequeue, fail to find the archive, and the user sees a delayed
    # failure instead of an immediate 404. The schema layer already guaranteed
    # archive_id is a non-empty string when source_type == "upload".
    if payload.metadata.get("source_type") == "upload":
        archive_id = str(payload.metadata.get("archive_id", ""))
        try:
            resolve_existing_archive(project.id, archive_id)
        except SourceArchiveError as exc:
            # ArchiveNotFound (404) — surface as a 404 scan error so the caller
            # learns the archive must be (re-)uploaded.
            raise ScanArchiveMissing(str(exc)) from exc

    # Phase 6 PR #19 — disk guard. Reject the scan up front when the
    # workspace volume is past DISK_HARD_LIMIT_PCT so the operator does
    # not get an in-flight Celery failure.
    _check_disk_guard()

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
    "ConcurrentScanLimitExceeded",
    "ProjectMissingForScan",
    "ScanArchiveMissing",
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
