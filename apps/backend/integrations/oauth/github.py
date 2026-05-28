"""
GitHub OAuth provider — Phase 8 PR #23.

Endpoints (https://docs.github.com/en/apps/oauth-apps/building-oauth-apps):
  - Authorize:  https://github.com/login/oauth/authorize
  - Token:      https://github.com/login/oauth/access_token
  - User:       https://api.github.com/user
  - Emails:     https://api.github.com/user/emails  (used when /user.email is null)

Why a separate /user/emails call?
  GitHub's ``/user`` endpoint returns ``email = null`` when the user's primary
  email is not public. We need a verified email to associate the OAuth
  identity, so we make a second call to ``/user/emails`` and pick the entry
  with ``primary == true && verified == true``. If no verified email exists
  the integration raises :class:`OAuthExchangeError` and the service redirects
  the user to the failure page with ``error=oauth_email_unverified``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from core.config import (
    github_oauth_client_id,
    github_oauth_client_secret,
    oauth_http_timeout_seconds,
)

from .base import (
    OAUTH_PROVIDER_GITHUB,
    OAuthExchangeError,
    OAuthProviderDisabled,
    OAuthUserInfo,
)

log = structlog.get_logger("integrations.oauth.github")

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"  # noqa: S105
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"

# We only need the user's basic profile + email. We deliberately avoid
# repository-level scopes — TrustedOSS reads code via the SCM webhook
# surface (Phase 5 PR #16), not via the OAuth login.
GITHUB_OAUTH_SCOPES = "read:user user:email"


class GitHubOAuthProvider:
    """Implements :class:`integrations.oauth.base.OAuthProvider` for GitHub."""

    name = OAUTH_PROVIDER_GITHUB

    def _require_credentials(self) -> tuple[str, str]:
        client_id = github_oauth_client_id()
        client_secret = github_oauth_client_secret()
        if not client_id or not client_secret:
            raise OAuthProviderDisabled("GitHub OAuth is not configured on this deployment")
        return client_id, client_secret

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        client_id, _ = self._require_credentials()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": GITHUB_OAUTH_SCOPES,
            "state": state,
            "allow_signup": "true",
        }
        return f"{GITHUB_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code_for_token(self, *, code: str, redirect_uri: str) -> str:
        client_id, client_secret = self._require_credentials()
        timeout = oauth_http_timeout_seconds()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    GITHUB_TOKEN_URL,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning(
                "oauth_github_exchange_network_failure",
                error_type=type(exc).__name__,
            )
            raise OAuthExchangeError("github token exchange network failure") from exc

        if response.status_code != 200:
            log.warning(
                "oauth_github_exchange_http_error",
                status=response.status_code,
            )
            raise OAuthExchangeError(
                f"github token exchange returned HTTP {response.status_code}",
            )

        payload: Any
        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthExchangeError("github token exchange returned non-JSON body") from exc

        if not isinstance(payload, dict):
            raise OAuthExchangeError("github token exchange returned a non-object body")

        # GitHub returns ``error`` / ``error_description`` on bad codes.
        if "error" in payload:
            log.warning(
                "oauth_github_exchange_provider_error",
                error_code=payload.get("error"),
            )
            raise OAuthExchangeError(
                f"github exchange error: {payload.get('error')!s}",
            )

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthExchangeError("github exchange returned no access_token")
        return access_token

    async def fetch_user_info(self, *, access_token: str) -> OAuthUserInfo:
        timeout = oauth_http_timeout_seconds()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                user_response = await client.get(GITHUB_USER_URL)
                if user_response.status_code != 200:
                    log.warning(
                        "oauth_github_userinfo_http_error",
                        status=user_response.status_code,
                    )
                    raise OAuthExchangeError(
                        f"github userinfo returned HTTP {user_response.status_code}",
                    )
                user_payload = user_response.json()

                # GitHub's `/user` returns `email = null` when the user keeps
                # their email private. Fall back to /user/emails and pick the
                # first verified primary entry.
                email = user_payload.get("email") if isinstance(user_payload, dict) else None
                if not isinstance(email, str) or not email:
                    emails_response = await client.get(GITHUB_EMAILS_URL)
                    if emails_response.status_code == 200:
                        email = _pick_primary_verified_email(emails_response.json())
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            log.warning(
                "oauth_github_userinfo_network_failure",
                error_type=type(exc).__name__,
            )
            raise OAuthExchangeError("github userinfo network failure") from exc

        if not isinstance(user_payload, dict):
            raise OAuthExchangeError("github userinfo returned a non-object body")

        provider_user_id = user_payload.get("id")
        if not isinstance(provider_user_id, int):
            raise OAuthExchangeError("github userinfo missing numeric id")

        if not email:
            raise OAuthExchangeError("github userinfo has no verified primary email")

        full_name = user_payload.get("name")
        if not isinstance(full_name, str) or not full_name.strip():
            full_name = None

        avatar_url = user_payload.get("avatar_url")
        if not isinstance(avatar_url, str) or not avatar_url:
            avatar_url = None

        return OAuthUserInfo(
            provider=OAUTH_PROVIDER_GITHUB,
            provider_user_id=str(provider_user_id),
            email=email.strip().lower(),
            full_name=full_name,
            avatar_url=avatar_url,
        )


def _pick_primary_verified_email(payload: Any) -> str | None:
    """Walk GitHub's /user/emails response and return the primary verified entry."""
    if not isinstance(payload, list):
        return None
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("primary") is True and entry.get("verified") is True:
            email = entry.get("email")
            if isinstance(email, str) and email:
                return email
    # Fallback: any verified entry, even non-primary.
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        if entry.get("verified") is True:
            email = entry.get("email")
            if isinstance(email, str) and email:
                return email
    return None


__all__ = [
    "GITHUB_AUTHORIZE_URL",
    "GITHUB_EMAILS_URL",
    "GITHUB_OAUTH_SCOPES",
    "GITHUB_TOKEN_URL",
    "GITHUB_USER_URL",
    "GitHubOAuthProvider",
]
