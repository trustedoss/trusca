"""
OAuth (GitHub + Google) authentication API — Phase 8 PR #23.

Endpoints under ``/auth/oauth``:
  - GET /auth/oauth/{provider}/authorize  (public — initiates the flow)
  - GET /auth/oauth/{provider}/callback   (public — provider posts back here)

Both endpoints are intentionally public per CLAUDE.md core rule #12:
the whole point of OAuth login is that the caller is anonymous.

The ``authorize`` endpoint returns a 302 redirect to the provider's consent
page with a signed CSRF state JWT in the query string. The ``callback``
endpoint exchanges the provider's ``code`` for an access token, fetches the
user, then either reuses an existing OAuth identity, links it to an existing
User by email, or creates a brand-new User + personal Team. On success it
sets the refresh-token HttpOnly cookie and 302s the user back to either
``state.redirect_after`` or the configured default landing URL.

Failures (invalid state, provider rejection, inactive user) redirect to
``OAUTH_LOGIN_REDIRECT_FAILURE`` with ``?error=oauth_<reason>`` so the SPA
can render an actionable message.

Error envelope: where the response is JSON (e.g. unknown provider, the
provider is not configured), we use RFC 7807 ``application/problem+json``.
The 302 paths cannot use Problem Details by definition; the failure URL +
``?error=...`` query parameter is the SPA contract.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import (
    app_env,
    oauth_login_redirect_default,
    oauth_login_redirect_failure,
    refresh_token_expire_days,
)
from core.db import get_db
from core.errors import problem_response
from services.oauth_service import (
    NoOrganizationConfigured,
    OAuthCallbackFailed,
    OAuthError,
    OAuthInvalidState,
    OAuthProviderUnavailable,
    OAuthProviderUnknown,
    OAuthUserInactive,
    complete_oauth,
    initiate_oauth,
)

router = APIRouter(prefix="/auth/oauth", tags=["auth"])
log = structlog.get_logger("oauth.api")

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/auth"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _problem_for_oauth_error(request: Request, exc: OAuthError) -> Response:
    """Translate an OAuthError into an RFC 7807 response."""
    extensions: dict[str, Any] = dict(exc.extensions)
    return problem_response(
        status_code=exc.status_code,
        title=exc.title,
        detail=str(exc) or exc.title,
        instance=request.url.path,
        **extensions,
    )


def _redirect_uri_for(request: Request, provider: str) -> str:
    """Compute the absolute redirect_uri for the provider callback.

    We cannot rely on a static config value because the deployment URL
    varies (localhost / staging / prod / behind a proxy). We use Starlette's
    ``request.url_for`` to build the canonical URL FastAPI registered for
    the callback route — this stays correct across reverse-proxy
    configurations as long as ``X-Forwarded-Proto`` / ``X-Forwarded-Host``
    are honoured by the ASGI server.
    """
    return str(request.url_for("oauth_callback", provider=provider))


def _set_refresh_cookie(response: Response, *, refresh_token: str) -> None:
    """Attach the refresh cookie. Mirrors :mod:`api.v1.auth`."""
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


def _failure_redirect(reason: str) -> RedirectResponse:
    """Build a 302 to the configured failure URL with ``?error=<reason>``."""
    base = oauth_login_redirect_failure()
    sep = "&" if "?" in base else "?"
    target = f"{base}{sep}{urlencode({'error': reason})}"
    return RedirectResponse(target, status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# GET /auth/oauth/{provider}/authorize  (PUBLIC)
# ---------------------------------------------------------------------------


@router.get(
    "/{provider}/authorize",
    summary="Begin OAuth sign-in (public)",
    name="oauth_authorize",
)
async def authorize(
    request: Request,
    provider: Literal["github", "google"],
    redirect_after: str | None = None,
) -> Response:
    """
    Public — no authentication required.

    302 → provider's authorize URL with a signed state. Returns:
      - 503 + ``oauth_provider_disabled`` Problem Details when the
        provider's client id/secret is not configured.
      - 404 Problem Details when the provider name is unknown (FastAPI's
        Literal[] gate normally catches this; the explicit branch survives
        future enum widening).
    """
    callback_uri = _redirect_uri_for(request, provider)
    try:
        url, _state = initiate_oauth(
            provider=provider,
            redirect_uri=callback_uri,
            redirect_after=redirect_after,
        )
    except OAuthProviderUnknown as exc:
        return _problem_for_oauth_error(request, exc)
    except OAuthProviderUnavailable as exc:
        return _problem_for_oauth_error(request, exc)

    log.info("oauth_authorize", provider=provider)
    return RedirectResponse(url, status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# GET /auth/oauth/{provider}/callback  (PUBLIC)
# ---------------------------------------------------------------------------


@router.get(
    "/{provider}/callback",
    summary="OAuth callback (public)",
    name="oauth_callback",
)
async def callback(
    request: Request,
    provider: Literal["github", "google"],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: AsyncSession = Depends(get_db),
) -> Response:
    """
    Public — the provider's redirect lands here after consent.

    Success path: 302 → ``redirect_after`` (or configured default) with the
    refresh-token HttpOnly cookie attached.

    Failure path: 302 → configured failure URL with ``?error=<reason>``. The
    provider's own ``?error=access_denied`` (user clicked "Cancel") falls
    through here too — we forward a normalised ``error=oauth_denied``.
    """
    if error:
        # User clicked "Cancel" on the consent page or the provider
        # otherwise declined. We do NOT log the raw provider error code
        # at INFO because it can be attacker-influenced; WARNING with a
        # truncated copy is enough for forensic correlation.
        log.warning("oauth_callback_provider_denied", provider=provider, error=error[:64])
        return _failure_redirect("oauth_denied")

    if not code or not state:
        log.warning("oauth_callback_missing_params", provider=provider)
        return _failure_redirect("oauth_missing_params")

    callback_uri = _redirect_uri_for(request, provider)

    try:
        user, _access_token, refresh_token, redirect_after = await complete_oauth(
            session,
            provider=provider,
            code=code,
            state=state,
            redirect_uri=callback_uri,
        )
    except OAuthInvalidState:
        log.warning("oauth_callback_invalid_state", provider=provider)
        return _failure_redirect("oauth_invalid_state")
    except OAuthProviderUnknown as exc:
        return _problem_for_oauth_error(request, exc)
    except OAuthProviderUnavailable as exc:
        return _problem_for_oauth_error(request, exc)
    except OAuthCallbackFailed:
        log.warning("oauth_callback_failed", provider=provider)
        return _failure_redirect("oauth_failed")
    except OAuthUserInactive:
        log.warning("oauth_callback_user_inactive", provider=provider)
        return _failure_redirect("oauth_user_inactive")
    except NoOrganizationConfigured:
        log.error("oauth_callback_no_organization", provider=provider)
        return _failure_redirect("oauth_no_organization")

    # Build the success redirect — fall back to the configured default if
    # the state did not carry an explicit ``redirect_after``. We do NOT
    # echo unsafe redirect_after values verbatim: the SPA is expected to
    # vet the value at sign-in time before placing it in the state JWT.
    target = redirect_after or oauth_login_redirect_default()
    response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
    _set_refresh_cookie(response, refresh_token=refresh_token)
    log.info("oauth_callback_success", provider=provider, user_id=str(user.id))
    return response


__all__ = ["router"]
