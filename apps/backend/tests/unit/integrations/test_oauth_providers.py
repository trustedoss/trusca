"""
Unit tests for ``integrations.oauth.{base,github,google}`` — Phase 8 PR #23.

These tests run without a DB. They mock the provider HTTP layer with
``httpx.MockTransport`` and verify:

  - ``authorize_url`` includes the configured client id, redirect uri,
    scope, and state.
  - ``exchange_code_for_token`` returns the access token on a 200 +
    JSON ``access_token`` payload.
  - ``fetch_user_info`` returns a typed :class:`OAuthUserInfo` and
    rejects malformed / unverified responses.
  - Both providers raise :class:`OAuthProviderDisabled` when the
    client id / secret is unset (CLAUDE.md core rule #11 + the 503
    Problem Details contract).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from integrations.oauth import (
    GitHubOAuthProvider,
    GoogleOAuthProvider,
    OAuthExchangeError,
    OAuthProviderDisabled,
    get_provider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def _patch_async_client(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[httpx.Request], httpx.Response]], None]:
    """Replace ``httpx.AsyncClient``'s transport with a MockTransport.

    Mirrors the pattern used in tests/unit/notifications/test_slack.py — we
    mutate __init__ instead of monkeypatching the class because the
    provider helpers instantiate ``httpx.AsyncClient`` themselves.
    """
    real_init = httpx.AsyncClient.__init__

    def install(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        def _patched_init(self: httpx.AsyncClient, *args, **kwargs) -> None:
            kwargs["transport"] = httpx.MockTransport(handler)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)

    return install


@pytest.fixture(autouse=True)
def _reset_oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default to fully-configured OAuth so each test opts INTO the disabled path."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "github-test-client-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "github-test-client-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-test-client-secret")


# ---------------------------------------------------------------------------
# get_provider
# ---------------------------------------------------------------------------


def test_get_provider_returns_github_implementation() -> None:
    prov = get_provider("github")
    assert isinstance(prov, GitHubOAuthProvider)
    assert prov.name == "github"


def test_get_provider_returns_google_implementation() -> None:
    prov = get_provider("google")
    assert isinstance(prov, GoogleOAuthProvider)
    assert prov.name == "google"


def test_get_provider_unknown_raises_value_error() -> None:
    with pytest.raises(ValueError):
        get_provider("facebook")


# ---------------------------------------------------------------------------
# GitHub: authorize_url
# ---------------------------------------------------------------------------


def test_github_authorize_url_embeds_client_id_state_and_scope() -> None:
    prov = GitHubOAuthProvider()
    url = prov.authorize_url(
        state="state-abc",
        redirect_uri="https://app.test/auth/oauth/github/callback",
    )
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "client_id=github-test-client-id" in url
    assert "state=state-abc" in url
    assert "scope=read%3Auser+user%3Aemail" in url
    assert "redirect_uri=https%3A%2F%2Fapp.test%2Fauth%2Foauth%2Fgithub%2Fcallback" in url


def test_github_authorize_url_raises_when_client_id_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    prov = GitHubOAuthProvider()
    with pytest.raises(OAuthProviderDisabled):
        prov.authorize_url(state="x", redirect_uri="https://x.test/cb")


def test_github_authorize_url_raises_when_client_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # client_id stays set; client_secret unset still trips the guard.
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "")
    prov = GitHubOAuthProvider()
    with pytest.raises(OAuthProviderDisabled):
        prov.authorize_url(state="x", redirect_uri="https://x.test/cb")


# ---------------------------------------------------------------------------
# GitHub: exchange_code_for_token
# ---------------------------------------------------------------------------


async def test_github_exchange_returns_access_token_on_200(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return httpx.Response(200, json={"access_token": "gh-tok-xyz", "token_type": "bearer"})

    _patch_async_client(handler)
    token = await GitHubOAuthProvider().exchange_code_for_token(
        code="abc", redirect_uri="https://x.test/cb"
    )
    assert token == "gh-tok-xyz"


async def test_github_exchange_raises_on_provider_error_payload(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "bad_verification_code"})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().exchange_code_for_token(
            code="bad", redirect_uri="https://x.test/cb"
        )


async def test_github_exchange_raises_on_http_500(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_github_exchange_raises_on_missing_token_field(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "bearer"})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


# ---------------------------------------------------------------------------
# GitHub: fetch_user_info
# ---------------------------------------------------------------------------


async def test_github_fetch_user_info_happy_path(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(
                200,
                json={
                    "id": 12345,
                    "email": "Octocat@github.com",
                    "name": "The Octocat",
                    "avatar_url": "https://cdn.test/avatar.png",
                },
            )
        raise AssertionError(f"unexpected url {request.url}")

    _patch_async_client(handler)
    info = await GitHubOAuthProvider().fetch_user_info(access_token="gh-tok")
    assert info.provider == "github"
    assert info.provider_user_id == "12345"
    # Email is downcased + stripped.
    assert info.email == "octocat@github.com"
    assert info.full_name == "The Octocat"
    assert info.avatar_url == "https://cdn.test/avatar.png"


async def test_github_fetch_user_info_falls_back_to_emails_endpoint(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 99, "email": None, "name": None})
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[
                    {"email": "non-primary@github.com", "primary": False, "verified": True},
                    {"email": "main@github.com", "primary": True, "verified": True},
                ],
            )
        raise AssertionError(f"unexpected url {request.url}")

    _patch_async_client(handler)
    info = await GitHubOAuthProvider().fetch_user_info(access_token="gh-tok")
    assert info.email == "main@github.com"
    assert info.provider_user_id == "99"
    assert info.full_name is None


async def test_github_fetch_user_info_rejects_missing_email(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 99, "email": None, "name": None})
        if request.url.path == "/user/emails":
            return httpx.Response(200, json=[])
        raise AssertionError(f"unexpected url {request.url}")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().fetch_user_info(access_token="gh-tok")


async def test_github_fetch_user_info_rejects_non_numeric_id(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "abc", "email": "x@y.com"})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().fetch_user_info(access_token="gh-tok")


# ---------------------------------------------------------------------------
# Google: authorize_url
# ---------------------------------------------------------------------------


def test_google_authorize_url_embeds_oidc_scopes_and_state() -> None:
    prov = GoogleOAuthProvider()
    url = prov.authorize_url(
        state="state-xyz",
        redirect_uri="https://app.test/auth/oauth/google/callback",
    )
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=google-test-client-id" in url
    assert "state=state-xyz" in url
    assert "scope=openid+email+profile" in url
    assert "response_type=code" in url
    assert "prompt=select_account" in url


def test_google_authorize_url_raises_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    with pytest.raises(OAuthProviderDisabled):
        GoogleOAuthProvider().authorize_url(state="x", redirect_uri="https://x.test/cb")


# ---------------------------------------------------------------------------
# Google: exchange + userinfo
# ---------------------------------------------------------------------------


async def test_google_exchange_returns_access_token(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "oauth2.googleapis.com"
        return httpx.Response(200, json={"access_token": "ya29.fake", "expires_in": 3600})

    _patch_async_client(handler)
    token = await GoogleOAuthProvider().exchange_code_for_token(
        code="x", redirect_uri="https://x.test/cb"
    )
    assert token == "ya29.fake"


async def test_google_fetch_user_info_happy_path(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "sub": "111222333",
                "email": "User@Example.Com",
                "email_verified": True,
                "name": "Demo User",
                "picture": "https://lh3.test/pic",
            },
        )

    _patch_async_client(handler)
    info = await GoogleOAuthProvider().fetch_user_info(access_token="ya29.fake")
    assert info.provider == "google"
    assert info.provider_user_id == "111222333"
    assert info.email == "user@example.com"
    assert info.full_name == "Demo User"
    assert info.avatar_url == "https://lh3.test/pic"


async def test_google_fetch_user_info_rejects_unverified_email(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "sub": "x",
                "email": "y@z.com",
                "email_verified": False,
                "name": "Untrusted",
            },
        )

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().fetch_user_info(access_token="ya29.fake")


async def test_google_fetch_user_info_rejects_missing_sub(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"email": "y@z.com", "email_verified": True}
        )

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().fetch_user_info(access_token="ya29.fake")


@pytest.mark.parametrize(
    "payload",
    [
        # Empty body
        {},
        # Sub present but email missing
        {"sub": "1", "email_verified": True},
        # All required keys but email is empty string
        {"sub": "1", "email": "", "email_verified": True},
    ],
)
async def test_google_fetch_user_info_adversarial_payloads(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
    payload: dict[str, object],
) -> None:
    """Adversarial input parametrize (MEMORY.md: feedback_adversarial_input_parametrize)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().fetch_user_info(access_token="ya29.fake")


# ---------------------------------------------------------------------------
# Network / timeout / non-JSON failure paths (parametrised across providers)
# ---------------------------------------------------------------------------


async def test_github_exchange_raises_on_network_failure(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_github_exchange_raises_on_timeout(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_github_exchange_raises_on_non_json_body(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_github_exchange_raises_on_non_object_body(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["array", "not", "object"])

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_github_userinfo_raises_on_user_endpoint_500(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().fetch_user_info(access_token="x")


async def test_github_userinfo_raises_on_network_failure(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().fetch_user_info(access_token="x")


async def test_github_emails_endpoint_503_falls_through_to_no_email_branch(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    """A 503 on /user/emails leaves email unset → OAuthExchangeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 5, "email": None, "name": "n"})
        if request.url.path == "/user/emails":
            return httpx.Response(503, json={})
        raise AssertionError(f"unexpected url {request.url}")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().fetch_user_info(access_token="x")


async def test_github_pick_email_falls_back_to_non_primary_verified(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    """No primary+verified entry; service falls back to any verified entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 7, "email": None, "name": "n"})
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[
                    {"email": "non-primary@gh.com", "primary": False, "verified": True},
                    # Bad shape — not a dict — must be skipped silently.
                    "garbage",
                ],
            )
        raise AssertionError(f"unexpected url {request.url}")

    _patch_async_client(handler)
    info = await GitHubOAuthProvider().fetch_user_info(access_token="x")
    assert info.email == "non-primary@gh.com"


async def test_github_pick_email_returns_none_when_no_verified_entries(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": 8, "email": None, "name": "n"})
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[
                    {"email": "unverified@gh.com", "primary": True, "verified": False},
                ],
            )
        raise AssertionError(f"unexpected url {request.url}")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GitHubOAuthProvider().fetch_user_info(access_token="x")


# ---------------------------------------------------------------------------
# Google failure-path coverage
# ---------------------------------------------------------------------------


async def test_google_exchange_raises_on_network_failure(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_google_exchange_raises_on_timeout(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_google_exchange_raises_on_500(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_google_exchange_raises_on_provider_error_payload(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "invalid_grant"})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_google_exchange_raises_on_non_json(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_google_exchange_raises_on_non_object(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2, 3])

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_google_exchange_raises_on_missing_token(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 3600})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().exchange_code_for_token(
            code="x", redirect_uri="https://x.test/cb"
        )


async def test_google_userinfo_raises_on_network(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().fetch_user_info(access_token="x")


async def test_google_userinfo_raises_on_500(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().fetch_user_info(access_token="x")


async def test_google_userinfo_raises_on_non_json(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().fetch_user_info(access_token="x")


async def test_google_userinfo_raises_on_non_object(
    _patch_async_client: Callable[[Callable[[httpx.Request], httpx.Response]], None],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1, 2])

    _patch_async_client(handler)
    with pytest.raises(OAuthExchangeError):
        await GoogleOAuthProvider().fetch_user_info(access_token="x")


# ---------------------------------------------------------------------------
# Google authorize_url — specific configurations
# ---------------------------------------------------------------------------


def test_google_authorize_url_raises_when_only_id_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id-only")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    with pytest.raises(OAuthProviderDisabled):
        GoogleOAuthProvider().authorize_url(state="x", redirect_uri="https://x.test/cb")
