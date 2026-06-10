"""
``/v1/users/me/*`` — caller-scoped self-service endpoints (Chore A2 + G).

This router groups endpoints that operate on the authenticated user's own
row (no ``user_id`` path parameter — the JWT IS the identifier).
Surfaces:

  - ``notification-prefs`` (Chore A2) — channel toggles.
  - ``oauth-identities``  (Chore G)  — list / unlink linked OAuth providers.

Auth: every endpoint requires :func:`get_current_user`. There is no
``user_id`` in the URL or body — even if the client supplies one in a stray
field, it is ignored because the service is keyed off ``actor.id``.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import CurrentUser, get_current_user
from schemas.notification import NotificationPrefsIn, NotificationPrefsOut
from schemas.oauth_identity import (
    OAuthIdentityListResponse,
    OAuthIdentityOut,
)
from services.notification_service import (
    get_or_create_prefs,
    update_prefs,
)
from services.oauth_identity_service import (
    OAuthIdentityNotFoundError,
    OAuthUnlinkBlocksLoginError,
    list_user_oauth_identities,
    unlink_oauth_identity,
    user_has_password,
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
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    """Full-row update — every channel field must be supplied.

    The body's only meaningful inputs are the four channel toggles. Any
    additional fields a caller may send (``user_id``, ``id``, ...) are
    ignored: Pydantic strips unknown fields by default and the service is
    keyed off ``actor.id``, never the body.

    Chore O / M3 — In-app notifications cannot be disabled. The frontend
    documents the in-app switch as "rendered but disabled"; this server-
    side guard closes the API drift where a direct PUT could opt out.
    """
    if not payload.in_app_enabled:
        return problem_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            title="In-app notifications are required",
            detail=(
                "The in-app channel cannot be disabled. Email, Slack, and "
                "Teams channels are individually opt-out, but in-app "
                "delivery is always on so the inbox remains a complete "
                "audit of notifications."
            ),
            instance=request.url.path,
            type_="urn:trustedoss:problem:notification_in_app_required",
        )
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


# ---------------------------------------------------------------------------
# GET /v1/users/me/oauth-identities
# ---------------------------------------------------------------------------


@router.get(
    "/oauth-identities",
    response_model=OAuthIdentityListResponse,
    summary="List the caller's connected OAuth identities (sorted oldest-first)",
)
async def list_oauth_identities(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    rows = await list_user_oauth_identities(session, user_id=actor.id)
    # M-16: ``has_password`` lets the SPA pre-disable Unlink on the last
    # identity of an OAuth-only account. ``CurrentUser`` is a light-weight
    # principal that (deliberately) does not carry ``hashed_password``, so
    # this is a single-column SELECT — the service shares its criterion
    # with the unlink 409 guard and never exposes the hash itself.
    body = OAuthIdentityListResponse(
        items=[OAuthIdentityOut.model_validate(row) for row in rows],
        has_password=await user_has_password(session, user_id=actor.id),
    )
    # ``by_alias=True`` honours the wire-shape aliases (``provider_email``,
    # ``created_at``) configured on :class:`OAuthIdentityOut`.
    return Response(
        content=body.model_dump_json(by_alias=True),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# DELETE /v1/users/me/oauth-identities/{id}
# ---------------------------------------------------------------------------


@router.delete(
    "/oauth-identities/{identity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink one of the caller's OAuth identities",
)
async def delete_oauth_identity(
    identity_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    """Remove an OAuth identity link from the authenticated user.

    Returns 204 on success. Domain failures map to RFC 7807:

      - 404 ``urn:trustedoss:problem:oauth_identity_not_found`` —
        identity does not exist OR belongs to another user
        (existence-hide; the two cases share a shape).
      - 409 ``urn:trustedoss:problem:oauth_unlink_blocks_login`` —
        unlinking would leave the user with no way to authenticate.
    """
    try:
        await unlink_oauth_identity(
            session,
            user_id=actor.id,
            identity_id=identity_id,
        )
    except OAuthIdentityNotFoundError as exc:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
            type_=exc.type_uri,
        )
    except OAuthUnlinkBlocksLoginError as exc:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
            type_=exc.type_uri,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
