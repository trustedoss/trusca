"""
Admin DT-Connector HTTP routes — Phase 4 PR #14.

Endpoints under ``/v1/admin/dt``:
  - GET  /v1/admin/dt/status                — breaker snapshot + DT version (cached)
  - GET  /v1/admin/dt/orphans               — paginated orphan-project list
  - POST /v1/admin/dt/orphans/cleanup       — enqueue Celery cleanup task
  - POST /v1/admin/dt/health-check          — synchronous probe + breaker tick

Auth: gated by the parent ``admin_router`` super-admin dependency. Anonymous
calls get 401; non-super-admin authenticated calls get 404 (existence-hide).

Service-layer 4xx/5xx (DT unreachable, cleanup-in-progress) translate to RFC
7807 Problem Details with snake_case extension fields and the canonical type
URI (``https://docs.trustedoss.io/errors/...``).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import get_audit_context
from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, require_super_admin_or_404
from models import AuditLog
from schemas.admin_ops import (
    BreakerResetOut,
    DTOrphanListPage,
    DTStatusOut,
    HealthProbeOut,
    OrphanCleanupEnqueued,
    OrphanCleanupRequest,
)
from services.admin_dt_service import (
    AdminDTError,
    enqueue_orphan_cleanup,
    force_health_check,
    force_reset_breaker,
    get_dt_status,
    list_orphans,
)

router = APIRouter(prefix="/dt", tags=["admin"])
log = structlog.get_logger("admin.dt.api")


def _problem_for_admin_dt_error(request: Request, exc: AdminDTError) -> Response:
    """Translate an AdminDTError into an RFC 7807 response with extensions."""
    extensions: dict[str, object] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        type_=exc.type_uri,
        **extensions,
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/dt/status
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    response_model=DTStatusOut,
    summary="DT health snapshot (admin) — breaker state + DT version, cached 30s",
)
async def get_status_endpoint(
    request: Request,  # noqa: ARG001
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    # Service runs synchronously (httpx + redis sync clients). Wrapping in
    # an async route is fine — FastAPI offloads sync work to the threadpool.
    result = get_dt_status()
    return Response(
        content=result.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/admin/dt/orphans
# ---------------------------------------------------------------------------


@router.get(
    "/orphans",
    response_model=DTOrphanListPage,
    summary="List orphan DT projects (admin) — DT projects with no matching local scan",
)
async def list_orphans_endpoint(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: CurrentUser = Depends(require_super_admin_or_404()),  # noqa: ARG001
) -> Response:
    try:
        page = list_orphans(limit=limit, offset=offset)
    except AdminDTError as exc:
        return _problem_for_admin_dt_error(request, exc)

    return Response(
        content=page.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/dt/orphans/cleanup
# ---------------------------------------------------------------------------


@router.post(
    "/orphans/cleanup",
    response_model=OrphanCleanupEnqueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue an orphan-cleanup Celery task (admin) — actually deletes DT projects",
)
async def cleanup_orphans_endpoint(
    request: Request,
    payload: OrphanCleanupRequest,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    # G6: empty list previously triggered a wipe-all (scan full DT catalog and
    # delete every orphan found). That is a footgun — a client accidentally
    # sending an empty body would mass-delete. "Cleanup all" is now a separate
    # explicit action; here we reject the empty case with 400.
    if not payload.dt_project_uuids:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Empty UUID List",
            detail=(
                "dt_project_uuids must contain at least one UUID. "
                "Retrieve the full orphan list via GET /v1/admin/dt/orphans, "
                "then submit the UUIDs explicitly."
            ),
            instance=request.url.path,
        )

    try:
        result = enqueue_orphan_cleanup(dt_project_uuids=list(payload.dt_project_uuids))
    except AdminDTError as exc:
        return _problem_for_admin_dt_error(request, exc)

    # Emit an audit row for the dispatch event itself. The task does its own
    # per-deletion audit rows from inside the Celery worker; this entry tells
    # the audit reader "an admin pressed the button at T".
    # G3: populate request_id / ip / user_agent from the middleware-injected
    # audit context so log correlation works for admin DT actions.
    _ctx = get_audit_context()
    audit = AuditLog(
        actor_user_id=actor.id,
        team_id=None,
        target_table="dt_projects",
        target_id=result.task_id or None,
        action="cleanup_enqueued",
        request_id=_ctx.get("request_id"),
        ip=_ctx.get("ip"),
        user_agent=_ctx.get("user_agent"),
        diff={
            "task_id": result.task_id,
            "count": result.count,
            "dt_project_uuids": [str(u) for u in payload.dt_project_uuids],
        },
    )
    session.add(audit)
    await session.commit()

    log.warning(
        "admin.dt.orphan_cleanup_enqueued",
        actor_id=str(actor.id),
        task_id=result.task_id,
        count=result.count,
    )

    return Response(
        content=result.model_dump_json(),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/dt/health-check
# ---------------------------------------------------------------------------


@router.post(
    "/health-check",
    response_model=HealthProbeOut,
    summary="Force a DT health probe (admin) — drives the breaker state",
)
async def force_health_check_endpoint(
    request: Request,  # noqa: ARG001
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    outcome = force_health_check()

    # Audit the operator-initiated probe so the audit log shows "admin X
    # forced a DT probe at T". The breaker mutation itself is in Redis, so
    # there is no domain row to drive the listener — emit an explicit row.
    # G3: populate request_id / ip / user_agent from middleware audit context.
    _ctx = get_audit_context()
    audit = AuditLog(
        actor_user_id=actor.id,
        team_id=None,
        target_table="dt_health",
        target_id=None,
        action="health_check",
        request_id=_ctx.get("request_id"),
        ip=_ctx.get("ip"),
        user_agent=_ctx.get("user_agent"),
        diff={
            "healthy": outcome.healthy,
            "state_before": outcome.state_before,
            "state_after": outcome.state_after,
            "fail_count": outcome.fail_count,
        },
    )
    session.add(audit)
    await session.commit()

    log.warning(
        "admin.dt.force_health_check",
        actor_id=str(actor.id),
        healthy=outcome.healthy,
        state_before=outcome.state_before,
        state_after=outcome.state_after,
    )

    return Response(
        content=outcome.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# POST /v1/admin/dt/breaker/reset — A4 manual sys-bug fix
# ---------------------------------------------------------------------------


@router.post(
    "/breaker/reset",
    response_model=BreakerResetOut,
    summary="Force the DT circuit breaker back to CLOSED (admin) — last-resort recovery",
)
async def reset_breaker_endpoint(
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(require_super_admin_or_404()),
) -> Response:
    """
    Operator escape hatch: when DT has recovered but the breaker is stuck OPEN
    (e.g. the cooldown window keeps tripping during a flaky restore), this
    endpoint forces it back to CLOSED + clears the failure counter.

    Refused with 409 + ``dt_breaker_already_closed`` extension when the
    breaker is already CLOSED — the operator should investigate why a reset
    looked necessary instead of letting a scripted retry no-op silently.
    """
    try:
        result = force_reset_breaker()
    except AdminDTError as exc:
        return _problem_for_admin_dt_error(request, exc)

    # The breaker mutation lives in Redis, so there is no domain row to
    # drive the audit listener — emit an explicit row using the same
    # request-context binding pattern as health-check / cleanup-enqueue.
    _ctx = get_audit_context()
    audit = AuditLog(
        actor_user_id=actor.id,
        team_id=None,
        target_table="dt_breaker",
        target_id=None,
        action="breaker_reset",
        request_id=_ctx.get("request_id"),
        ip=_ctx.get("ip"),
        user_agent=_ctx.get("user_agent"),
        diff={
            "state_before": result.state_before,
            "state_after": result.state_after,
            "fail_count_before": result.fail_count_before,
        },
    )
    session.add(audit)
    await session.commit()

    log.warning(
        "admin.dt.breaker_reset",
        actor_id=str(actor.id),
        state_before=result.state_before,
        state_after=result.state_after,
        fail_count_before=result.fail_count_before,
    )

    return Response(
        content=result.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
