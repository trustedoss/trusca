"""
OAuth provider integrations — Phase 8 PR #23.

This package exposes a narrow :class:`OAuthProvider` protocol and concrete
implementations for GitHub and Google. The :mod:`services.oauth_service`
layer is the only intended caller — the router never talks to a provider
directly.

Why a protocol per provider?
  - Each provider quirks the exchange shape (GitHub returns
    ``access_token=...&token_type=bearer`` x-www-form-urlencoded by default
    unless you ask for JSON; Google uses OIDC ``sub`` while GitHub uses a
    numeric ``id``). Centralising the differences here keeps the service
    logic provider-agnostic.
  - It makes the test surface trivially mockable (``httpx.MockTransport``
    against the provider's known endpoints).
"""

from __future__ import annotations

from .base import (
    OAUTH_PROVIDER_GITHUB,
    OAUTH_PROVIDER_GOOGLE,
    OAuthExchangeError,
    OAuthProvider,
    OAuthProviderDisabled,
    OAuthUserInfo,
    get_provider,
)
from .github import GitHubOAuthProvider
from .google import GoogleOAuthProvider

__all__ = [
    "GitHubOAuthProvider",
    "GoogleOAuthProvider",
    "OAUTH_PROVIDER_GITHUB",
    "OAUTH_PROVIDER_GOOGLE",
    "OAuthExchangeError",
    "OAuthProvider",
    "OAuthProviderDisabled",
    "OAuthUserInfo",
    "get_provider",
]
