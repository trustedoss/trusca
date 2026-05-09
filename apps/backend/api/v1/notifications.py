"""
In-app notification center API — Chore A2.

Endpoints:
  GET   /v1/notifications                List notifications for the caller.
  PATCH /v1/notifications/{id}/read      Mark one notification as read.
  PATCH /v1/notifications/read-all       Mark all unread as read.
  GET   /v1/notifications/unread-count   Bell badge count.

All 4xx / 5xx responses use ``application/problem+json`` (RFC 7807) per
CLAUDE.md §4. Every endpoint requires authentication via
:func:`get_current_user` — there is no public path here. The endpoint layer
is a thin HTTP adapter; the canonical RBAC ("this row belongs to me") lives
inside :mod:`services.notification_service`.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, get_current_user
from schemas.notification import (
    NotificationListResponse,
    NotificationOut,
    UnreadCountOut,
)
from services.notification_service import (
    NotificationError,
    count_unread,
    list_notifications,
    mark_all_read,
    mark_read,
)

router = APIRouter(prefix="/v1/notifications", tags=["notifications"])
log = structlog.get_logger("notifications.api")


def _problem_for(request: Request, exc: NotificationError) -> Response:
    """Translate a domain error into an RFC 7807 ``problem+json`` envelope."""
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


# ---------------------------------------------------------------------------
# GET /v1/notifications
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="Paginated list of notifications for the authenticated user",
)
async def list_notifications_endpoint(
    request: Request,
    unread_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    rows, total, unread = await list_notifications(
        session,
        user_id=actor.id,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )
    body = NotificationListResponse(
        items=[NotificationOut.model_validate(r) for r in rows],
        total=total,
        unread_count=unread,
        page=page,
        page_size=page_size,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# GET /v1/notifications/unread-count
# ---------------------------------------------------------------------------


@router.get(
    "/unread-count",
    response_model=UnreadCountOut,
    summary="Unread notification count for the authenticated user (bell badge)",
)
async def unread_count_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    count = await count_unread(session, user_id=actor.id)
    body = UnreadCountOut(count=count)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/notifications/read-all
# ---------------------------------------------------------------------------


# read-all is registered BEFORE /{notification_id}/read so the static path
# wins the FastAPI route resolution: a literal "read-all" segment would
# otherwise be matched against the UUID parameter and 422 out as an
# invalid uuid string.
@router.patch(
    "/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark all of the caller's unread notifications as read",
)
async def mark_all_read_endpoint(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    rowcount = await mark_all_read(session, user_id=actor.id)
    log.info(
        "notifications.mark_all_read",
        user_id=str(actor.id),
        affected=rowcount,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# PATCH /v1/notifications/{notification_id}/read
# ---------------------------------------------------------------------------


@router.patch(
    "/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark a single notification as read (idempotent)",
    responses={
        204: {"description": "Marked as read (or was already read — idempotent)."},
        404: {
            "description": (
                "Notification does not exist OR belongs to a different user "
                "(existence-hide)."
            )
        },
    },
)
async def mark_read_endpoint(
    request: Request,
    notification_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    try:
        await mark_read(
            session,
            user_id=actor.id,
            notification_id=notification_id,
        )
    except NotificationError as exc:
        return _problem_for(request, exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
