"""
Pydantic schemas for the public OAuth provider-availability endpoint — M-15.

Public shape (frozen contract — the anonymous /login page depends on it):

  - ``OAuthProviderStatusOut``  — one provider row (name + configured bool).
  - ``OAuthProvidersResponse``  — wrapper (``providers: [...]``).

Why a separate module from :mod:`schemas.oauth_identity`?
  - ``oauth_identity`` describes the *authenticated* self-service surface
    (``/v1/users/me/oauth-identities``). This module describes the
    *anonymous* pre-login surface (``GET /auth/oauth/providers``). Keeping
    them split keeps each concern's wire shape obvious — same split as the
    routers (``api.v1.oauth`` vs ``api.v1.users_me``).

Security: the response intentionally carries ONLY a boolean per provider.
Client ids, secrets, redirect URLs, or any other configuration detail must
never be added here — the consumer is unauthenticated.
"""

from __future__ import annotations

from pydantic import BaseModel

from schemas.oauth_identity import OAuthProvider


class OAuthProviderStatusOut(BaseModel):
    """Availability of a single OAuth provider for sign-in.

    ``configured`` is ``True`` only when both the client id and client
    secret are set — the precondition for the /authorize flow to work
    (see :func:`services.oauth_service.oauth_provider_configured`).
    """

    provider: OAuthProvider
    configured: bool


class OAuthProvidersResponse(BaseModel):
    """Response wrapper for ``GET /auth/oauth/providers``.

    Always lists every supported provider (stable order: github, google),
    each with a bare ``configured`` boolean, so the /login page can decide
    which sign-in buttons to render BEFORE the user authenticates.
    """

    providers: list[OAuthProviderStatusOut]


__all__ = [
    "OAuthProviderStatusOut",
    "OAuthProvidersResponse",
]
