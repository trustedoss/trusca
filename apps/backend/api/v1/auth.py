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

import structlog
from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit import audit_context
from core.config import access_token_expire_minutes, app_env, refresh_token_expire_days
from core.db import get_db
from core.errors import problem_response
from core.ratelimit import LOGIN_RATE_LIMIT, limiter
from core.security import CurrentUser, get_current_user
from schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserPublic,
)
from services.auth_service import (
    AuthError,
    EmailAlreadyExists,
    InvalidCredentials,
    InvalidRefreshToken,
    RefreshReuseDetected,
    authenticate,
    issue_token_pair,
    register_user,
    revoke_refresh,
    rotate_refresh,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger("auth.api")

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/auth"


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
    Public — no authentication required.

    Returns the new user (without password). 422 for validation errors,
    409 if the email is already registered.
    """
    try:
        user = await register_user(
            session,
            email=str(payload.email),
            password=payload.password,
            full_name=payload.full_name,
        )
    except EmailAlreadyExists as exc:
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
    user = await authenticate(session, email=str(payload.email), password=payload.password)
    if user is None:
        exc = InvalidCredentials("invalid email or password")
        return _problem_for_auth_error(request, exc)

    # Bind audit context so the listener has the actor for last_login_at update.
    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(user.id)
    audit_context.set(ctx)

    access_token, refresh_token, _ = await issue_token_pair(session, user=user)

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
    summary="Rotate refresh token (public; refresh cookie is the credential)",
)
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
    response_model=UserPublic,
    summary="Return the currently authenticated user",
)
async def me(
    current_user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UserPublic:
    """Authenticated. Returns the same shape as /auth/register."""
    from sqlalchemy import select

    from models import User

    result = await session.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one()
    return UserPublic.model_validate(user)
