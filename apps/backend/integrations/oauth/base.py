# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
OAuthProvider protocol + shared types — Phase 8 PR #23.

Every concrete provider implements three methods:
  - :meth:`OAuthProvider.authorize_url` — build the redirect URL the SPA
    sends the user to.
  - :meth:`OAuthProvider.exchange_code_for_token` — swap the ``?code=``
    parameter received on /callback for an access token.
  - :meth:`OAuthProvider.fetch_user_info` — read the provider's
    canonical user record (id + email + display name + avatar).

The service consumes :class:`OAuthUserInfo` exclusively, so adding a new
provider only requires implementing the protocol — no service changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

# ---------------------------------------------------------------------------
# Closed provider set
# ---------------------------------------------------------------------------

ProviderName = Literal["github", "google", "oidc"]

# Module-level Literal constants — annotated with the same Literal type so
# mypy treats them as the narrowed value, not a plain ``str``. Required for
# direct passthrough into :class:`OAuthUserInfo` whose ``provider`` field is
# also Literal-typed.
OAUTH_PROVIDER_GITHUB: ProviderName = "github"
OAUTH_PROVIDER_GOOGLE: ProviderName = "google"
#: The deployment's own identity provider, configured by issuer rather
#: than named here. One entry, not a list: see integrations/oauth/oidc.py.
OAUTH_PROVIDER_OIDC: ProviderName = "oidc"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OAuthExchangeError(Exception):
    """Provider-side failure during code-exchange or userinfo fetch.

    The service translates this into a 502 Problem Details (or a 302 redirect
    to the SPA's failure URL) — never a 500. The original cause is logged
    server-side; clients only see a generic error code.
    """


class OAuthProviderDisabled(Exception):
    """Raised when ``CLIENT_ID`` / ``CLIENT_SECRET`` are unset for a provider.

    The router maps this to 503 with extension ``oauth_provider_disabled``
    so the SPA can hide the disabled button on the /login page.
    """


# ---------------------------------------------------------------------------
# Provider-agnostic user info shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OAuthUserInfo:
    """Canonical user record returned by every provider after exchange.

    Attributes:
        provider: ``'github'`` or ``'google'`` — drives the
            ``oauth_identities.provider`` column.
        provider_user_id: Provider-stable id. NEVER use email here — users
            change their primary email; the stable id does not.
        email: Verified email reported by the provider. May differ from
            ``users.email`` (e.g. GitHub no-reply addresses).
        full_name: Display name (``None`` if the provider does not surface
            one — Google guarantees, GitHub does not).
        avatar_url: Optional profile image URL.
        email_can_link_existing_account: whether this address is trustworthy
            enough to sign the holder into an account that already exists
            under it. True for providers that verify the address themselves.
            False when the deployment configured a non-standard claim or
            waived verification: in both cases the address is a claim the
            user can influence, and matching it against an existing account
            would hand that account to whoever asserts it.
    """

    provider: ProviderName
    provider_user_id: str
    email: str
    full_name: str | None
    avatar_url: str | None
    email_can_link_existing_account: bool = True


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class OAuthProvider(Protocol):
    """Per-provider adapter. Implementations live in github.py / google.py."""

    name: ProviderName

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        """Return the URL the browser should be redirected to for consent.

        ``state`` is the signed JWT from :func:`services.oauth_service.initiate_oauth`
        and MUST be echoed back by the provider on /callback. ``redirect_uri``
        is the absolute URL of our /callback endpoint and must match the value
        registered in the provider's developer console.
        """
        ...

    async def exchange_code_for_token(
        self,
        *,
        code: str,
        redirect_uri: str,
    ) -> str:
        """Swap the OAuth ``code`` query parameter for an access token.

        Raises :class:`OAuthProviderDisabled` if the client id/secret is not
        configured. Raises :class:`OAuthExchangeError` for any provider-side
        failure (4xx, 5xx, network timeout, malformed response).
        """
        ...

    async def fetch_user_info(self, *, access_token: str) -> OAuthUserInfo:
        """Fetch the canonical :class:`OAuthUserInfo` using the access token.

        Raises :class:`OAuthExchangeError` for any provider-side failure or
        when the provider returns a row missing required fields (no email,
        no id).
        """
        ...


# ---------------------------------------------------------------------------
# Provider lookup
# ---------------------------------------------------------------------------


def get_provider(name: str) -> OAuthProvider:
    """Return the provider implementation for ``name``.

    Raises ``ValueError`` for unknown providers — the router validates the
    path parameter via Literal so this should be unreachable in practice,
    but the explicit check makes accidental call-sites fail loudly instead
    of silently downgrading to ``None``.
    """
    # Local imports — avoid the circular ``__init__ -> github/google -> base``
    # chain that would trip when the providers import :class:`OAuthProvider`
    # before this module finishes initialising.
    from .github import GitHubOAuthProvider
    from .google import GoogleOAuthProvider
    from .oidc import OidcProvider

    if name == OAUTH_PROVIDER_GITHUB:
        return GitHubOAuthProvider()
    if name == OAUTH_PROVIDER_GOOGLE:
        return GoogleOAuthProvider()
    if name == OAUTH_PROVIDER_OIDC:
        return OidcProvider()
    raise ValueError(f"unknown OAuth provider: {name!r}")


__all__ = [
    "OAUTH_PROVIDER_GITHUB",
    "OAUTH_PROVIDER_GOOGLE",
    "OAUTH_PROVIDER_OIDC",
    "OAuthExchangeError",
    "OAuthProvider",
    "OAuthProviderDisabled",
    "OAuthUserInfo",
    "ProviderName",
    "get_provider",
]
