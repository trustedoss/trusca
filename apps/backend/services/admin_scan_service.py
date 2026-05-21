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

from core.audit import bind_audit_team
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


def _revoke_transport_errors() -> tuple[type[BaseException], ...]:
    """Exception types ``celery.control.revoke`` may raise on a broker outage.

    Imported lazily so the service module stays importable without kombu /
    redis present (pure unit tests of unrelated helpers), mirroring the lazy
    ``from tasks.celery_app import celery_app`` inside the revoke path.

    - ``kombu.exceptions.OperationalError`` — kombu's declared transport error,
      raised when the broker connection cannot be established / used.
    - ``redis.exceptions.RedisError`` — when the redis transport surfaces its
      own error before kombu wraps it.
    - ``OSError`` — raw socket-level failure (connection refused / reset).

    Programming errors (TypeError / AttributeError / ValueError) are
    intentionally absent so they propagate (Low #5).
    """
    errors: list[type[BaseException]] = [OSError]
    try:
        from kombu.exceptions import OperationalError as _KombuOperationalError

        errors.append(_KombuOperationalError)
    except ImportError:  # pragma: no cover - kombu always present with celery
        pass
    try:
        from redis.exceptions import RedisError as _RedisError

        errors.append(_RedisError)
    except ImportError:  # pragma: no cover - redis always present with celery
        pass
    return tuple(errors)


# Resolved once at import: the worker process (and the API process that issues
# the revoke) always have kombu + redis available. Kept as a module value, not
# an env-derived constant, so rule #11 (runtime os.getenv) is not implicated.
_REVOKE_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = _revoke_transport_errors()


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
    Cancel a running / queued scan (super-admin — cross-team).

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
    scan = await _lock_cancellable_scan(session, scan_id)
    return await _revoke_and_mark_cancelled(
        session,
        actor=actor,
        scan=scan,
        reason="cancelled by admin",
        celery_app_override=celery_app_override,
        log_event="admin.scan.cancelled",
    )


async def cancel_scan_for_actor(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    scan_id: uuid.UUID,
    celery_app_override: Any | None = None,
) -> AdminScanListItem:
    """
    Cancel a running / queued scan the *actor's own team* owns (PR-A1).

    This is the user-facing counterpart to :func:`cancel_scan`. It reuses the
    same revoke + status-mutation core (so the two paths never drift on
    idempotency / race handling) but adds a team-access gate:

      - **Other team's scan → 404** (``AdminScanNotFound``). We deliberately
        existence-hide rather than 403: a developer who is not on the owning
        team must not be able to distinguish "this scan exists but you can't
        touch it" from "no such scan", which would leak cross-team scan ids.
        super_admin bypasses the team gate (parity with admin tooling).
      - Missing scan → 404 (same as admin).
      - Already terminal → 409 (``ScanAlreadyCancelled``).

    The team check happens AFTER the row lock so a concurrent cancel cannot
    slip a TOCTOU window between the access check and the status mutation.
    """
    scan = await _lock_cancellable_scan(session, scan_id)

    # Team-access gate. Resolve the owning team from the parent project. We do
    # this while holding the row lock so the access decision and the mutation
    # are in the same transaction.
    team_id = (
        await session.execute(
            select(Project.team_id).where(Project.id == scan.project_id)
        )
    ).scalar_one_or_none()
    if not _actor_can_access_team(actor, team_id):
        # Existence-hide: same shape as a non-existent scan.
        raise AdminScanNotFound(f"scan {scan_id} not found")

    # M1: bind the owning team to the audit ContextVar BEFORE the mutating
    # commit so the audit_logs row for the status='cancelled' update carries a
    # non-NULL team_id. Same pattern as services.scan_service._bind_audit_team
    # (project_service uses it too); we resolved team_id above for the access
    # gate, so this is a free re-use rather than an extra query. ``team_id`` is
    # guaranteed non-None here: a None team_id fails _actor_can_access_team for
    # non-admins, and for super_admin the FK from scan -> project -> team means
    # the only way it is None is a project deleted underfoot (best-effort skip).
    if team_id is not None:
        bind_audit_team(team_id)

    return await _revoke_and_mark_cancelled(
        session,
        actor=actor,
        scan=scan,
        reason="cancelled by user",
        celery_app_override=celery_app_override,
        log_event="scan.user_cancelled",
    )


def _actor_can_access_team(actor: CurrentUser, team_id: uuid.UUID | None) -> bool:
    """True when ``actor`` may act on resources owned by ``team_id``.

    super_admin / superuser always pass. Everyone else must have the team in
    their membership list. A ``None`` team_id (project vanished underfoot)
    is treated as no-access for non-admins.

    Low #3 (policy note): scan cancellation is intentionally *membership*-gated
    (any team member, i.e. developer) — NOT team_admin-gated like project
    writes (``project_service._can_write_project``). A developer may cancel
    their own team's scan by confirmed policy; this is deliberately a weaker
    gate than mutating project settings. Hence we check ``team_id in
    actor.team_ids`` (membership) rather than ``actor.team_roles[...] ==
    "team_admin"`` (role).
    """
    if actor.is_superuser or actor.role == "super_admin":
        return True
    if team_id is None:
        return False
    return team_id in actor.team_ids


async def _lock_cancellable_scan(session: AsyncSession, scan_id: uuid.UUID) -> Scan:
    """Row-lock the scan and assert it is not already terminal.

    Shared by the admin + user cancel paths so the 404 / 409 semantics and the
    ``with_for_update`` race protection are defined exactly once. The lock
    means two concurrent cancel calls cannot both pass the terminal-state
    guard (CWE-362).
    """
    stmt = select(Scan).where(Scan.id == scan_id).with_for_update()
    scan = (await session.execute(stmt)).scalar_one_or_none()
    if scan is None:
        raise AdminScanNotFound(f"scan {scan_id} not found")
    if scan.status in _TERMINAL_STATUSES:
        raise ScanAlreadyCancelled(
            f"scan {scan_id} is already in terminal state {scan.status!r}"
        )
    return scan


async def _revoke_and_mark_cancelled(
    session: AsyncSession,
    *,
    actor: CurrentUser,
    scan: Scan,
    reason: str,
    celery_app_override: Any | None,
    log_event: str,
) -> AdminScanListItem:
    """Revoke the Celery task and stamp the scan ``cancelled``.

    Extracted from the original admin ``cancel_scan`` body so the user-facing
    path shares identical revoke + commit + response-shaping behaviour. The
    only caller-controlled differences are the ``error_message`` (``reason``)
    and the structlog event name.
    """
    scan_id = scan.id
    # Revoke the Celery task BEFORE mutating the row so that if the worker
    # is mid-flight, the SIGTERM lands while the row is still 'running'
    # (the worker's own progress hooks / `finally` reclaim the workspace).
    if scan.celery_task_id:
        if celery_app_override is not None:
            celery = celery_app_override
        else:
            from tasks.celery_app import celery_app

            celery = celery_app
        try:
            celery.control.revoke(scan.celery_task_id, terminate=True, signal="SIGTERM")
        except _REVOKE_TRANSPORT_ERRORS as exc:
            # Low #5: best-effort, but ONLY for broker / transport failures.
            #
            # ``control.revoke`` publishes a broadcast control message over the
            # broker; when the broker is unreachable kombu surfaces a
            # ``kombu.exceptions.OperationalError`` (its declared transport
            # error), and the underlying redis / socket layer can raise
            # ``redis.exceptions.RedisError`` / ``OSError``. Those are the
            # transient conditions we deliberately swallow: the scan must still
            # be marked ``cancelled`` so the user is not stuck on a broker
            # hiccup, and the workspace cleaner + hard-limit backstop reclaim
            # the slot regardless.
            #
            # We do NOT catch bare ``Exception`` any more: a TypeError /
            # AttributeError / ValueError here means a programming error in the
            # call (wrong arg, bad celery_app_override) and must surface in
            # tests rather than masquerade as a transient broker outage.
            log.warning(
                "scan.revoke_failed",
                scan_id=str(scan_id),
                celery_task_id=scan.celery_task_id,
                error=str(exc),
            )

    now = _now()
    scan.status = "cancelled"
    scan.completed_at = now
    scan.error_message = reason
    scan.updated_at = now

    await session.commit()
    await session.refresh(scan)

    log.warning(
        log_event,
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
    "cancel_scan_for_actor",
    "list_scans",
]
