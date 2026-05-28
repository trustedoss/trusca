"""
Google OAuth (OIDC) provider — Phase 8 PR #23.

Endpoints (OpenID Connect Discovery — values pinned for stability):
  - Authorize:  https://accounts.google.com/o/oauth2/v2/auth
  - Token:      https://oauth2.googleapis.com/token
  - Userinfo:   https://openidconnect.googleapis.com/v1/userinfo

We use the ``openid email profile`` scope set, which gives us the standard
OIDC claims (``sub``, ``email``, ``email_verified``, ``name``, ``picture``).
Refusing the sign-in if ``email_verified`` is ``false`` is mandatory — Google
allows unverified emails on free workspaces and we do not want to link a
TrustedOSS account to an unverifiable address.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from core.config import (
    google_oauth_client_id,
    google_oauth_client_secret,
    oauth_http_timeout_seconds,
)

from .base import (
    OAUTH_PROVIDER_GOOGLE,
    OAuthExchangeError,
    OAuthProviderDisabled,
    OAuthUserInfo,
)

log = structlog.get_logger("integrations.oauth.google")

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

GOOGLE_OAUTH_SCOPES = "openid email profile"


class GoogleOAuthProvider:
    """Implements :class:`integrations.oauth.base.OAuthProvider` for Google."""

    name = OAUTH_PROVIDER_GOOGLE

    def _require_credentials(self) -> tuple[str, str]:
        client_id = google_oauth_client_id()
        client_secret = google_oauth_client_secret()
        if not client_id or not client_secret:
            raise OAuthProviderDisabled("Google OAuth is not configured on this deployment")
        return client_id, client_secret

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        client_id, _ = self._require_credentials()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_OAUTH_SCOPES,
            "state": state,
            # ``access_type=online`` because we never need a refresh token
            # from Google — TrustedOSS issues its OWN refresh JWT after the
            # initial sign-in. Asking for ``offline`` would pop a more
            # invasive consent screen for no benefit.
            "access_type": "online",
            "include_granted_scopes": "true",
            # Force account picker so users with multiple Google accounts
            # are not accidentally signed in as the wrong one.
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code_for_token(self, *, code: str, redirect_uri: str) -> str:
        client_id, client_secret = self._require_credentials()
        timeout = oauth_http_timeout_seconds()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    GOOGLE_TOKEN_URL,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": redirect_uri,
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning(
                "oauth_google_exchange_network_failure",
                error_type=type(exc).__name__,
            )
            raise OAuthExchangeError("google token exchange network failure") from exc

        if response.status_code != 200:
            log.warning(
                "oauth_google_exchange_http_error",
                status=response.status_code,
            )
            raise OAuthExchangeError(
                f"google token exchange returned HTTP {response.status_code}",
            )

        payload: Any
        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthExchangeError("google token exchange returned non-JSON body") from exc

        if not isinstance(payload, dict):
            raise OAuthExchangeError("google token exchange returned a non-object body")

        if "error" in payload:
            log.warning(
                "oauth_google_exchange_provider_error",
                error_code=payload.get("error"),
            )
            raise OAuthExchangeError(
                f"google exchange error: {payload.get('error')!s}",
            )

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthExchangeError("google exchange returned no access_token")
        return access_token

    async def fetch_user_info(self, *, access_token: str) -> OAuthUserInfo:
        timeout = oauth_http_timeout_seconds()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                response = await client.get(GOOGLE_USERINFO_URL)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning(
                "oauth_google_userinfo_network_failure",
                error_type=type(exc).__name__,
            )
            raise OAuthExchangeError("google userinfo network failure") from exc

        if response.status_code != 200:
            log.warning(
                "oauth_google_userinfo_http_error",
                status=response.status_code,
            )
            raise OAuthExchangeError(
                f"google userinfo returned HTTP {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthExchangeError("google userinfo returned non-JSON body") from exc

        if not isinstance(payload, dict):
            raise OAuthExchangeError("google userinfo returned a non-object body")

        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise OAuthExchangeError("google userinfo missing 'sub' claim")

        email = payload.get("email")
        if not isinstance(email, str) or not email:
            raise OAuthExchangeError("google userinfo missing 'email' claim")

        # email_verified MUST be true — see module docstring.
        if payload.get("email_verified") is not True:
            log.warning("oauth_google_email_unverified", sub=sub)
            raise OAuthExchangeError("google userinfo reports email_verified=false")

        full_name = payload.get("name")
        if not isinstance(full_name, str) or not full_name.strip():
            full_name = None

        avatar_url = payload.get("picture")
        if not isinstance(avatar_url, str) or not avatar_url:
            avatar_url = None

        return OAuthUserInfo(
            provider=OAUTH_PROVIDER_GOOGLE,
            provider_user_id=sub,
            email=email.strip().lower(),
            full_name=full_name,
            avatar_url=avatar_url,
        )


__all__ = [
    "GOOGLE_AUTHORIZE_URL",
    "GOOGLE_OAUTH_SCOPES",
    "GOOGLE_TOKEN_URL",
    "GOOGLE_USERINFO_URL",
    "GoogleOAuthProvider",
]
