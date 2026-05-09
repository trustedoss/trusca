"""
``/v1/users/me/*`` — caller-scoped self-service endpoints (Chore A2).

This router groups endpoints that operate on the authenticated user's own
row (no ``user_id`` path parameter — the JWT IS the identifier). Today it
only exposes ``notification-prefs`` (Chore A2); future self-service surfaces
(profile edits, language, timezone, etc.) belong here too.

Auth: every endpoint requires :func:`get_current_user`. There is no
``user_id`` in the URL or body — even if the client supplies one in a stray
field, it is ignored because the service is keyed off ``actor.id``.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.security import CurrentUser, get_current_user
from schemas.notification import NotificationPrefsIn, NotificationPrefsOut
from services.notification_service import (
    get_or_create_prefs,
    update_prefs,
)

router = APIRouter(prefix="/v1/users/me", tags=["users-me"])
log = structlog.get_logger("users_me.api")


# ---------------------------------------------------------------------------
# GET /v1/users/me/notification-prefs
# ---------------------------------------------------------------------------


@router.get(
    "/notification-prefs",
    response_model=NotificationPrefsOut,
    summary="Return the caller's notification preferences (creates defaults)",
)
async def get_notification_prefs(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    prefs = await get_or_create_prefs(session, user_id=actor.id)
    body = NotificationPrefsOut.model_validate(prefs)
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# PUT /v1/users/me/notification-prefs
# ---------------------------------------------------------------------------


@router.put(
    "/notification-prefs",
    response_model=NotificationPrefsOut,
    summary="Replace the caller's notification preferences (full-row PUT)",
)
async def put_notification_prefs(
    payload: NotificationPrefsIn,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    """Full-row update — every channel field must be supplied.

    The body's only meaningful inputs are the four channel toggles. Any
    additional fields a caller may send (``user_id``, ``id``, ...) are
    ignored: Pydantic strips unknown fields by default and the service is
    keyed off ``actor.id``, never the body.
    """
    prefs = await update_prefs(
        session,
        user_id=actor.id,
        email_enabled=payload.email_enabled,
        slack_enabled=payload.slack_enabled,
        teams_enabled=payload.teams_enabled,
        in_app_enabled=payload.in_app_enabled,
    )
    body = NotificationPrefsOut.model_validate(prefs)
    log.info(
        "notifications.prefs_updated",
        user_id=str(actor.id),
        email=payload.email_enabled,
        slack=payload.slack_enabled,
        teams=payload.teams_enabled,
        in_app=payload.in_app_enabled,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


__all__ = ["router"]
