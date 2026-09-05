# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Authentication API — Phase 1 PR #5.

Endpoints under `/auth`:
  - POST /auth/register  (public — explicit per CLAUDE.md core rule #12)
  - POST /auth/login     (public — rate limited 5/min/IP)
  - POST /auth/refresh   (public — refresh cookie validates the caller)
  - POST /auth/logout    (auth optional)
  - GET  /auth/me        (auth required)

Error responses use RFC 7807 (`application/problem+json`) via the helper in
core.errors. Domain exceptions from services/auth_service.py are translated
into the appropriate status+title here so callers never see Python tracebacks.

Refresh tokens travel as an `HttpOnly` + `SameSite=Lax` cookie scoped to
`/auth`. The cookie is `Secure` only when APP_ENV=prod so dev (HTTP) still
works against `localhost` browsers.
"""

from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import audit_context
from core.config import (
    access_token_expire_minutes,
    app_env,
    password_reset_confirm_rate_limit,
    password_reset_email_cooldown_seconds,
    password_reset_request_rate_limit,
    refresh_rate_limit,
    refresh_token_expire_days,
)
from core.db import get_db
from core.errors import problem_response
from core.login_throttle import (
    clear,
    record_failure,
    release_spend,
    seconds_until_retry,
    spend_attempt,
    spend_once,
)
from core.ratelimit import LOGIN_RATE_LIMIT, limiter
from core.security import (
    MFA_PENDING_EXPIRE_MINUTES,
    TOKEN_TYPE_MFA_PENDING,
    CurrentUser,
    create_mfa_pending_token,
    decode_token,
    get_current_user,
)
from models import User
from schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MembershipPublic,
    MfaVerifyRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserMeResponse,
    UserPublic,
)
from services.auth_service import (
    AuthError,
    EmailAlreadyExists,
    InvalidCredentials,
    InvalidRefreshToken,
    RefreshReuseDetected,
    RegistrationClosed,
    StaleCredential,
    authenticate,
    issue_token_pair,
    register_user,
    revoke_refresh,
    rotate_refresh,
)
from services.mfa_service import MfaError, verify_second_factor
from services.password_reset_service import (
    InvalidResetToken,
    PasswordResetError,
    consume_reset_token,
    request_password_reset,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger("auth.api")

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/auth"


def _mfa_required_response(user: User) -> Response:
    """202 with a pending token: the password was right, a code is owed.

    Not 200, because nothing was signed in; not 401, because nothing was
    refused. The body carries the pending token rather than a cookie, so
    nothing about this response resembles a session.
    """
    body = {
        "mfa_required": True,
        "mfa_token": create_mfa_pending_token(
            subject=str(user.id), token_type=TOKEN_TYPE_MFA_PENDING
        ),
        "expires_in": MFA_PENDING_EXPIRE_MINUTES * 60,
    }
    return Response(
        content=json.dumps(body),
        media_type="application/json",
        status_code=status.HTTP_202_ACCEPTED,
    )


def _throttled_response(request: Request, retry_after: int) -> Response:
    """429 with Retry-After, saying when rather than why.

    Identical for an address that exists and one that does not, so it cannot be
    used to enumerate accounts. What it can say is when to come back, which is
    what somebody who mistyped their password needs, and the sign-in form pairs
    it with an offer to reset.

    Built by the shared problem helper rather than by hand: the RFC 7807 shape
    is defined in one place, and a second copy of it here would drift.
    """
    response = problem_response(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        title="Too Many Attempts",
        detail=("Too many failed sign-in attempts. Wait and try again, or reset " "your password."),
        instance=str(request.url.path),
        type_="https://trustedoss.dev/problems/too-many-attempts",
        retry_after_seconds=retry_after,
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


def _problem_for_auth_error(request: Request, exc: AuthError) -> Response:
    """Translate an AuthError into an RFC 7807 response."""
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
    )


def _set_refresh_cookie(response: Response, *, refresh_token: str) -> None:
    """
    Attach the refresh cookie. HttpOnly + SameSite=Lax always; Secure only in
    prod so dev over plain HTTP still works.
    """
    is_prod = app_env() == "prod"
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=refresh_token_expire_days() * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=is_prod,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
    )


# ---------------------------------------------------------------------------
# Register (PUBLIC — exempt from auth per CLAUDE.md rule #12)
# ---------------------------------------------------------------------------


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user (public)",
)
async def register(
    request: Request,
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """
    Public, no authentication required.

    Returns the new user (without password). 422 for validation errors, 409 if
    the email is already registered, and 404 when the deployment maintains its
    roster itself (``AUTH_SELF_REGISTRATION=false``), which is the same answer
    an outsider gets for any route that is not there.
    """
    try:
        user = await register_user(
            session,
            email=str(payload.email),
            password=payload.password,
            full_name=payload.full_name,
        )
    except (EmailAlreadyExists, RegistrationClosed) as exc:
        return _problem_for_auth_error(request, exc)

    public = UserPublic.model_validate(user)
    return Response(
        content=public.model_dump_json(),
        status_code=status.HTTP_201_CREATED,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Login (PUBLIC — rate limited)
# ---------------------------------------------------------------------------


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login (public, rate limited)",
)
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """
    Public — no authentication required, but limited to 5 attempts/minute/IP.

    On success: 200 + access_token in the body, refresh as HttpOnly cookie.
    On bad credentials: 401 problem+json.
    """
    # Refused before the password is looked at, and refused the same way
    # whether or not the address belongs to anybody: a reply that differed
    # would answer "does this account exist" for free, which is a wider
    # question than this endpoint is here to answer.
    #
    # Returning here rather than after `authenticate` is part of that, not a
    # saved bcrypt. Reaching the credential check makes the reply's timing
    # depend on whether the address exists, which is the leak the dummy hash in
    # `authenticate` exists to close, and it lets somebody being refused go on
    # spending the server's time. Deleting this to simplify the flow would put
    # both back.
    retry_after = await seconds_until_retry(str(payload.email))
    if retry_after > 0:
        return _throttled_response(request, retry_after)

    user = await authenticate(session, email=str(payload.email), password=payload.password)
    if user is None:
        # Counted for addresses that exist and addresses that do not, for the
        # same reason. The call returns how long the address is now refused,
        # which the caller is told rather than left to discover by retrying.
        wait = await record_failure(str(payload.email))
        if wait > 0:
            return _throttled_response(request, wait)
        exc = InvalidCredentials("invalid email or password")
        return _problem_for_auth_error(request, exc)

    # Bind audit context so the listener has the actor for last_login_at update.
    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(user.id)
    audit_context.set(ctx)

    if user.mfa_enabled:
        # No session yet. What goes back proves the password and nothing else,
        # and the type check refuses it on every other route, so a client that
        # ignores the code screen has nothing to ignore it with.
        return _mfa_required_response(user)

    try:
        # The account has no second factor, which is why the branch above did
        # not return. Saying so rather than letting the minting function
        # assume it: the assumption is what an added arrival path inherits.
        access_token, refresh_token, _ = await issue_token_pair(
            session, user=user, second_factor_satisfied=True
        )
    except StaleCredential as exc:
        # A password change committed between the bcrypt check above and the
        # session being written. Refusing is the point: the reset's sweep has
        # already run, so a session opened here would be one it could not have
        # revoked. 401 sends the caller back to the form, where the new
        # password works.
        return _problem_for_auth_error(request, exc)

    # Cleared here, not after the password check, because this is where the
    # request has actually succeeded. The version above cleared on "password
    # correct", which meant the StaleCredential refusal a few lines up returned
    # 401 with the count already zeroed.
    #
    # The second factor arrived, and this is the line that comment warned
    # about. It stays below the branch that hands out a pending token: with MFA
    # on, a correct password is not a success and must not refill the budget,
    # or somebody who knows the password gets unlimited attempts at the code.
    await clear(str(payload.email))

    body = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=access_token_expire_minutes() * 60,
    )
    response = Response(
        content=body.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
    _set_refresh_cookie(response, refresh_token=refresh_token)
    return response


# slowapi's `@limiter.limit` wraps the endpoint with functools.wraps. That
# preserves __annotations__ but the wrapper's __globals__ still points at
# slowapi's own module — so when FastAPI calls typing.get_type_hints() on
# the wrapper to resolve string annotations created by `from __future__
# import annotations`, names like `LoginRequest` and `AsyncSession` cannot
# be resolved and FastAPI misclassifies the body as a query parameter,
# returning 422 for every request.
#
# Patch the wrapper's `__globals__` reference to point at this module so
# get_type_hints can find the names. We can't reassign `__globals__`
# directly (it's read-only), but we *can* mutate the globals dict in place,
# so we add the missing names to whatever globals() the wrapper inherits
# from. This is enough for FastAPI's `get_type_hints(func, globalns=
# func.__globals__)` lookup to succeed.
for _name in ("LoginRequest", "AsyncSession", "Request", "Response", "Depends"):
    if _name in globals():
        login.__globals__.setdefault(_name, globals()[_name])
del _name


# ---------------------------------------------------------------------------
# Refresh (PUBLIC — refresh cookie is the credential)
# ---------------------------------------------------------------------------


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token (public; refresh cookie is the credential, rate limited)",
)
@limiter.limit(refresh_rate_limit())
async def refresh(
    request: Request,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """
    Public — the refresh cookie is the credential.

    Successful rotation: 200 + new access_token + new refresh cookie.
    Reuse detected (cookie already rotated): 401, entire chain revoked.
    """
    try:
        access_token, new_refresh, _, user = await rotate_refresh(
            session, raw_refresh=refresh_token or ""
        )
    except RefreshReuseDetected as exc:
        return _problem_for_auth_error(request, exc)
    except InvalidRefreshToken as exc:
        return _problem_for_auth_error(request, exc)

    # Audit context: bind the user so this rotation is attributed.
    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(user.id)
    audit_context.set(ctx)

    body = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=access_token_expire_minutes() * 60,
    )
    response = Response(
        content=body.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
    _set_refresh_cookie(response, refresh_token=new_refresh)
    return response


@router.post(
    "/mfa/verify",
    response_model=TokenResponse,
    summary="Finish a sign-in by supplying the second factor (public, rate limited)",
)
@limiter.limit(LOGIN_RATE_LIMIT)
async def mfa_verify(
    request: Request,
    payload: MfaVerifyRequest,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """
    Public. The pending token from `POST /auth/login` is the credential, and it
    is worth nothing on its own.

    On success: 200 + access_token, refresh as an HttpOnly cookie. This is the
    first point at which a session exists.
    """
    try:
        claims = decode_token(payload.mfa_token, expected_type=TOKEN_TYPE_MFA_PENDING)
    except (JWTError, ValueError):
        return _problem_for_auth_error(request, InvalidCredentials("invalid or expired"))

    user = await session.get(User, uuid.UUID(str(claims["sub"])))
    if user is None or not user.is_active:
        return _problem_for_auth_error(request, InvalidCredentials("invalid or expired"))

    jti = str(claims["jti"])
    window = MFA_PENDING_EXPIRE_MINUTES * 60

    # A token is worth a few tries, not one and not unlimited.
    #
    # Spending it before the code is checked was the first shape of this, and
    # it made a single mistyped digit end the sign-in: the step stays on screen
    # asking for the next code, and no code can ever be right, because the
    # token is gone. Six digits read off a phone get mistyped, so that is the
    # common path rather than the rare one.
    #
    # Unlimited is the other end and is worse: five minutes of guessing at one
    # in a million is not a wall on its own, and the per-address counter is
    # shared with passwords rather than dedicated to this.
    if not await spend_attempt(jti, seconds=window):
        log.warning("auth.mfa_token_attempts_exhausted", user_id=str(user.id))
        await spend_once(jti, seconds=window)
        return _problem_for_auth_error(request, InvalidCredentials("invalid or expired"))

    # The single-use claim, taken before the code is checked and given back if
    # the code is wrong.
    #
    # Taken first because two requests racing with the same token must not both
    # proceed; given back because otherwise this is the mistyped-digit defect
    # again by another route. Taken AFTER the code is checked was the second
    # shape and it was also wrong: a replayed token with a fresh valid code
    # spent that code and then answered 401, so a client retrying after a
    # network timeout lost a recovery code and saw an error.
    if not await spend_once(jti, seconds=window):
        log.warning("auth.mfa_token_replayed", user_id=str(user.id))
        return _problem_for_auth_error(request, InvalidCredentials("invalid or expired"))

    # Same counter the password attempts went into, keyed by the same address.
    # Two counters would mean an attacker could take each to just under its
    # threshold, doubling what the limit is supposed to allow; and counting
    # only passwords would leave the code itself unlimited, which for six
    # digits is a matter of time rather than of difficulty.
    retry_after = await seconds_until_retry(user.email)
    if retry_after > 0:
        return _throttled_response(request, retry_after)

    try:
        await verify_second_factor(session, user=user, code=payload.code)
    except MfaError:
        # The claim goes back, so the next code from the app can use the same
        # token. What stops this being unlimited is the attempt counter above,
        # which does not go back.
        await release_spend(jti)
        wait = await record_failure(user.email)
        if wait > 0:
            return _throttled_response(request, wait)
        return _problem_for_auth_error(request, InvalidCredentials("invalid code"))

    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(user.id)
    audit_context.set(ctx)

    try:
        # ``verify_second_factor`` returned without raising, a few lines up.
        access_token, refresh_token, _ = await issue_token_pair(
            session, user=user, second_factor_satisfied=True
        )
    except StaleCredential as exc:
        return _problem_for_auth_error(request, exc)

    # Here, not after the password. This is the success the throttle counts.
    await clear(user.email)

    body = TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=access_token_expire_minutes() * 60,
    )
    response = Response(
        content=body.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )
    _set_refresh_cookie(response, refresh_token=refresh_token)
    return response


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Logout (revoke refresh cookie)",
)
async def logout(
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """
    Revoke the refresh cookie. Idempotent — always returns 204 even if the
    cookie is absent or already revoked.
    """
    await revoke_refresh(session, raw_refresh=refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(response)
    return response


# ---------------------------------------------------------------------------
# Me (AUTH REQUIRED)
# ---------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=UserMeResponse,
    summary="Return the currently authenticated user and their memberships",
)
async def me(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UserMeResponse:
    """Authenticated. UserPublic + the caller's team memberships.

    The frontend reads ``memberships`` to resolve a ``team_id`` for project
    creation / write scoping. Ordered oldest-first so ``memberships[0]`` is a
    stable default team.
    """
    from sqlalchemy import select

    from models import Membership, Team, User

    result = await session.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()

    rows = await session.execute(
        select(Membership.team_id, Team.name, Membership.role)
        .join(Team, Team.id == Membership.team_id)
        .where(Membership.user_id == current_user.id)
        .order_by(Membership.created_at.asc())
    )
    memberships = [
        MembershipPublic(team_id=team_id, team_name=team_name, role=str(role))
        for team_id, team_name, role in rows.all()
    ]

    # Validate the base shape from the ORM row (UserPublic has no relationship
    # fields), then attach memberships explicitly. Validating UserMeResponse
    # directly would make Pydantic read user.memberships (a lazy ORM
    # relationship) and trigger async IO outside the greenlet (MissingGreenlet).
    base = UserPublic.model_validate(user)
    return UserMeResponse(
        **base.model_dump(), memberships=memberships, mfa_enabled=bool(user.mfa_enabled)
    )


# ---------------------------------------------------------------------------
# Forgot password (PUBLIC — rate limited)
#
# CWE-204 contract: ALWAYS returns 204 regardless of whether the email
# exists. The service-layer handles the timing-equivalent dummy work + the
# Celery email enqueue when the email matches. See
# :mod:`services.password_reset_service` for the design notes.
# ---------------------------------------------------------------------------


@router.post(
    "/forgot-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Request a password reset link (public, rate limited)",
)
@limiter.limit(password_reset_request_rate_limit())
async def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Public — no authentication required, but limited per
    ``PASSWORD_RESET_RATE_LIMIT`` (5/min/IP by default).

    Body shape: ``{"email": "<address>"}``. Always returns 204 + empty body
    (CWE-204). When the address matches a registered user we additionally
    enqueue an email via Celery. When the per-email cooldown is active we
    set ``Retry-After`` to the configured cooldown and STILL return 204.
    """
    outcome = await request_password_reset(session, email=str(payload.email))

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    if outcome.get("cooldown_active") and outcome.get("retry_after_seconds"):
        response.headers["Retry-After"] = str(outcome["retry_after_seconds"])
    elif not outcome.get("matched"):
        # Surface the configured cooldown as Retry-After even on the
        # negative-match branch so an external observer cannot distinguish
        # "your email is not registered" from "the cooldown is active".
        response.headers["Retry-After"] = str(password_reset_email_cooldown_seconds())
    return response


# Slowapi wrapper preservation — same hack as ``login`` above. The forgot
# endpoint also accepts a Pydantic body, so without this fix FastAPI's
# get_type_hints lookup misclassifies the body as a query parameter.
for _name in (
    "ForgotPasswordRequest",
    "AsyncSession",
    "Request",
    "Response",
    "Depends",
):
    if _name in globals():
        forgot_password.__globals__.setdefault(_name, globals()[_name])
del _name


# ---------------------------------------------------------------------------
# Reset password (PUBLIC — token IS the credential)
# ---------------------------------------------------------------------------


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm a password reset using a one-shot token (public, rate limited)",
)
@limiter.limit(password_reset_confirm_rate_limit())
async def reset_password(
    request: Request,
    payload: ResetPasswordRequest,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Public — the reset token is the credential.

    Limited to ``PASSWORD_RESET_CONFIRM_RATE_LIMIT`` (5/min/IP by default,
    same as login): the token is guessable the same way a password is, and
    the verify path costs a bcrypt call per live candidate, so this
    endpoint needs no less protection than login (F1,
    concurrency-scaling-plan-2026-08-22.md).

    On success: 204 + every refresh token for the user is revoked.
    On bad / expired / used token: 422 problem+json.
    """
    try:
        await consume_reset_token(
            session,
            plaintext_token=payload.token,
            new_password=payload.new_password,
        )
    except InvalidResetToken as exc:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
        )
    except PasswordResetError as exc:
        return problem_response(
            status_code=exc.status_code,
            title=exc.title,
            detail=str(exc) or exc.title,
            instance=request.url.path,
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Slowapi wrapper preservation, same hack as ``login`` and ``forgot_password``
# above. This endpoint also accepts a Pydantic body, so without this fix
# FastAPI's get_type_hints lookup misclassifies the body as a query
# parameter.
for _name in (
    "ResetPasswordRequest",
    "AsyncSession",
    "Request",
    "Response",
    "Depends",
):
    if _name in globals():
        reset_password.__globals__.setdefault(_name, globals()[_name])
del _name
