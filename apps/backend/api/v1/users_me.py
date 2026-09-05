# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
``/v1/users/me/*`` — caller-scoped self-service endpoints (Chore A2 + G).

This router groups endpoints that operate on the authenticated user's own
row (no ``user_id`` path parameter — the JWT IS the identifier).
Surfaces:

  - ``notification-prefs`` (Chore A2) — channel toggles.
  - ``oauth-identities``  (Chore G)  — list / unlink linked OAuth providers.
  - ``export``            (ER32)     - take a copy of one's own personal data.

Auth: every endpoint requires :func:`get_current_user`. There is no
``user_id`` in the URL or body — even if the client supplies one in a stray
field, it is ignored because the service is keyed off ``actor.id``.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from core.errors import problem_response
from core.security import (
    TOKEN_TYPE_MFA_ENROLLING,
    CurrentUser,
    create_mfa_pending_token,
    decode_token,
    get_current_user,
)
from models import User
from schemas.auth import (
    MfaEnrolCompleteRequest,
    MfaEnrolStartResponse,
    MfaStepUpRequest,
    RecoveryCodesResponse,
)
from schemas.notification import NotificationPrefsIn, NotificationPrefsOut
from schemas.oauth_identity import (
    OAuthIdentityListResponse,
    OAuthIdentityOut,
)
from services.mfa_service import (
    InvalidMfaCode,
    MfaAlreadyEnabled,
    MfaNotEnrolled,
    ReauthenticationRequired,
    begin_enrolment,
    complete_enrolment,
    reauthenticate,
    regenerate_recovery_codes,
)
from services.notification_service import (
    create_notification,
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
from services.user_export_service import build_self_export

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


# ---------------------------------------------------------------------------
# GET /v1/users/me/export
# ---------------------------------------------------------------------------
@router.get(
    "/export",
    summary="Download a copy of your own personal data",
    responses={
        200: {
            "description": (
                "The personal data held about the caller. Work product "
                "(projects, scans, findings, policies) is not included: it "
                "belongs to the organisation, not to the individual. The "
                "activity section reports its own total and says when it has "
                "been capped."
            ),
            "content": {"application/json": {}},
        },
    },
)
async def export_self(
    session: AsyncSession = Depends(get_db),
    actor: CurrentUser = Depends(get_current_user),
) -> Response:
    """Keyed off the JWT, never off a parameter.

    There is no ``user_id`` anywhere in this route. An export endpoint that
    accepted one would be an endpoint for reading other people's personal
    data, guarded only by whatever check somebody remembered to write.
    """
    payload = await build_self_export(session, user_id=actor.id)
    if payload is None:
        # The token authenticated against a row that no longer exists.
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="Not Found",
            detail="No account for this token.",
            instance="/v1/users/me/export",
        )
    return JSONResponse(
        content=payload,
        headers={
            # Named for the person, dated, so a support inbox holding several
            # of these can tell them apart.
            "Content-Disposition": (
                f'attachment; filename="trusca-export-{actor.id}.json"'
            )
        },
    )


# ---------------------------------------------------------------------------
# Second factor
#
# Enrolment is two calls, not one. The first stores a secret and returns it to
# be scanned; the second proves the authenticator produced a code from it and
# only then turns the factor on. Doing it in one call means anybody who closes
# the tab between scanning and confirming is locked out of their own account at
# the next sign-in, asked for a code their app was never set up to make.
# ---------------------------------------------------------------------------


async def _step_up_or_problem(
    request: Request,
    session: AsyncSession,
    *,
    user: User,
    payload: MfaStepUpRequest,
) -> Response | None:
    """``None`` when the person proved themselves, a 401 problem otherwise.

    One shape for both routes, so the answer cannot differ between them. The
    status is 401 rather than 403: the session is fine, what is missing is a
    fresh proof, and a client that reads 403 as "this account may not" would
    hide the retry rather than offer it.
    """
    try:
        await reauthenticate(
            session, user=user, password=payload.password, code=payload.code
        )
    except ReauthenticationRequired:
        # Deliberately the same answer for a wrong password, a wrong code and
        # nothing supplied. Distinguishing them tells whoever holds a stolen
        # session which of the two proofs is worth attacking.
        log.warning("auth.mfa_step_up_failed", user_id=str(user.id))
        return problem_response(
            status_code=status.HTTP_401_UNAUTHORIZED,
            title="Confirm It Is You",
            detail=(
                "enter your current password, or a code from your "
                "authenticator app, to continue"
            ),
            instance=str(request.url.path),
        )
    return None


@router.post(
    "/mfa/enrol",
    response_model=MfaEnrolStartResponse,
    summary="Start enrolling a second factor",
)
async def start_mfa_enrolment(
    request: Request,
    payload: MfaStepUpRequest,
    session: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Store a secret and hand back what the setup screen needs to show it.

    Behind a step-up. Enrolling on somebody else's account is a takeover: the
    attacker's authenticator becomes the factor, and the owner is locked out
    by the control they never set up.
    """
    user = await session.get(User, current_user.id)
    if user is None:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="User Not Found",
            detail="user not found",
            instance=str(request.url.path),
        )

    step_up = await _step_up_or_problem(request, session, user=user, payload=payload)
    if step_up is not None:
        return step_up

    try:
        secret, uri = await begin_enrolment(session, user=user)
    except MfaAlreadyEnabled:
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="Already Enabled",
            detail=(
                "a second factor is already enabled; clear it before enrolling "
                "again"
            ),
            instance=str(request.url.path),
        )

    body = MfaEnrolStartResponse(
        secret=secret,
        provisioning_uri=uri,
        # A separate type from the sign-in one. If they were the same, starting
        # an enrolment would mint something that finishes a sign-in, which is a
        # way past the very factor being enrolled.
        mfa_token=create_mfa_pending_token(
            subject=str(user.id), token_type=TOKEN_TYPE_MFA_ENROLLING
        ),
    )
    return Response(
        content=body.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/mfa/enrol/confirm",
    response_model=RecoveryCodesResponse,
    summary="Finish enrolling by proving the authenticator works",
)
async def confirm_mfa_enrolment(
    request: Request,
    payload: MfaEnrolCompleteRequest,
    session: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Turn the factor on and return the recovery codes, shown once."""
    try:
        claims = decode_token(payload.mfa_token, expected_type=TOKEN_TYPE_MFA_ENROLLING)
    except (JWTError, ValueError):
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid Enrolment Token",
            detail="the enrolment has expired; start again",
            instance=str(request.url.path),
        )

    # The token names a user and the request carries a session. They have to be
    # the same person: without this, somebody could finish their own enrolment
    # against another account's session, or the reverse.
    if str(claims.get("sub")) != str(current_user.id):
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid Enrolment Token",
            detail="the enrolment does not belong to this session",
            instance=str(request.url.path),
        )

    user = await session.get(User, current_user.id)
    if user is None:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="User Not Found",
            detail="user not found",
            instance=str(request.url.path),
        )

    try:
        codes = await complete_enrolment(session, user=user, code=payload.code)
    except MfaAlreadyEnabled:
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="Already Enabled",
            detail="a second factor is already enabled",
            instance=str(request.url.path),
        )
    except MfaNotEnrolled:
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="No Enrolment In Progress",
            detail="start the enrolment again",
            instance=str(request.url.path),
        )
    except InvalidMfaCode:
        return problem_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            title="Invalid Code",
            detail="that code did not match; check the time on your device",
            instance=str(request.url.path),
        )

    # Told, not just logged. Somebody who did not set this up needs to find
    # out that their account now asks for a code, and the only place they will
    # look is the portal. Written after the enrolment landed, so a notice can
    # never describe something that did not happen.
    await create_notification(
        session,
        user_id=user.id,
        kind="account_security",
        title="Two-step sign-in is now on",
        body=(
            "An authenticator app was set up on your account. If that was not "
            "you, change your password and tell your administrator: whoever "
            "did it can sign in as you."
        ),
        link="/profile",
        target_table="users",
        target_id=user.id,
    )
    body = RecoveryCodesResponse(codes=codes)
    return Response(
        content=body.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )


@router.post(
    "/mfa/recovery-codes",
    response_model=RecoveryCodesResponse,
    summary="Replace the unused recovery codes with a fresh set",
)
async def regenerate_mfa_recovery_codes(
    request: Request,
    payload: MfaStepUpRequest,
    session: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    """Issue a new set, invalidating every unused code from the old one.

    Behind a step-up, because what it hands back is ten sign-ins that bypass
    the factor, survive a password change, and are not touched by revoking
    sessions. Gating that on a session alone means a stolen token is worth ten
    of them, and a stolen session is the case the factor exists to survive.
    """
    user = await session.get(User, current_user.id)
    if user is None:
        return problem_response(
            status_code=status.HTTP_404_NOT_FOUND,
            title="User Not Found",
            detail="user not found",
            instance=str(request.url.path),
        )

    step_up = await _step_up_or_problem(request, session, user=user, payload=payload)
    if step_up is not None:
        return step_up

    try:
        codes = await regenerate_recovery_codes(session, user=user)
    except MfaNotEnrolled:
        return problem_response(
            status_code=status.HTTP_409_CONFLICT,
            title="Not Enabled",
            detail="no second factor is enabled",
            instance=str(request.url.path),
        )

    # The codes just issued are ten sign-ins that bypass the factor, and the
    # old set stopped working. Somebody who did not ask for that has to hear
    # about it.
    await create_notification(
        session,
        user_id=user.id,
        kind="account_security",
        title="New recovery codes were issued",
        body=(
            "A fresh set of recovery codes was created for your account and "
            "the previous set no longer works. If that was not you, change "
            "your password and tell your administrator."
        ),
        link="/profile",
        target_table="users",
        target_id=user.id,
    )
    body = RecoveryCodesResponse(codes=codes)
    return Response(
        content=body.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
