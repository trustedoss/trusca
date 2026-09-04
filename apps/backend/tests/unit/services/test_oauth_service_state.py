"""
Unit tests for ``services.oauth_service`` state-JWT helpers — Phase 8 PR #23.

The DB-bound branches (resolve / create user, personal-team bootstrap) are
covered by the integration test ``tests/integration/test_oauth_api.py``;
these tests focus on the pure CSRF state path which has no DB dependency.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from jose import jwt

from core.security import JWT_ALGORITHM


@pytest.fixture(autouse=True)
def _stable_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin SECRET_KEY so state minted in one test verifies in the next."""
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret-key-min-32-chars-XXXXXXXX")


# ---------------------------------------------------------------------------
# _signed_state / _decode_state round-trip
# ---------------------------------------------------------------------------


def test_signed_state_round_trips_provider_and_redirect_after() -> None:
    from services.oauth_service import (
        STATE_TOKEN_TYPE,
        _decode_state,
        _signed_state,
    )

    state = _signed_state(provider="github", redirect_after="/projects/abc")
    claims = _decode_state(state, expected_provider="github")

    assert claims["type"] == STATE_TOKEN_TYPE
    assert claims["provider"] == "github"
    assert claims["redirect_after"] == "/projects/abc"
    assert "nonce" in claims
    assert "exp" in claims
    assert "iat" in claims


def test_signed_state_nonce_is_unique_per_call() -> None:
    """Two consecutive states must carry distinct nonces (CSRF replay)."""
    from services.oauth_service import _decode_state, _signed_state

    a = _signed_state(provider="github", redirect_after=None)
    b = _signed_state(provider="github", redirect_after=None)
    assert a != b
    nonce_a = _decode_state(a, expected_provider="github")["nonce"]
    nonce_b = _decode_state(b, expected_provider="github")["nonce"]
    assert nonce_a != nonce_b


def test_signed_state_omits_redirect_after_when_none() -> None:
    from services.oauth_service import _decode_state, _signed_state

    state = _signed_state(provider="google", redirect_after=None)
    claims = _decode_state(state, expected_provider="google")
    assert "redirect_after" not in claims


# ---------------------------------------------------------------------------
# _decode_state failure cases
# ---------------------------------------------------------------------------


def test_decode_state_rejects_empty_token() -> None:
    from services.oauth_service import OAuthInvalidState, _decode_state

    with pytest.raises(OAuthInvalidState):
        _decode_state("", expected_provider="github")


def test_decode_state_rejects_garbage_signature() -> None:
    from services.oauth_service import OAuthInvalidState, _decode_state

    with pytest.raises(OAuthInvalidState):
        _decode_state("not-a-real-jwt", expected_provider="github")


def test_decode_state_rejects_provider_mismatch() -> None:
    """A state minted for github must NOT validate on the google callback."""
    from services.oauth_service import OAuthInvalidState, _decode_state, _signed_state

    state = _signed_state(provider="github", redirect_after=None)
    with pytest.raises(OAuthInvalidState):
        _decode_state(state, expected_provider="google")


def test_decode_state_rejects_wrong_token_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """A regular access JWT must NOT be accepted as an OAuth state."""
    from core.config import secret_key
    from services.oauth_service import OAuthInvalidState, _decode_state

    # Hand-mint a JWT with type='access' (not 'oauth_state').
    now = int(time.time())
    token = jwt.encode(
        {
            "type": "access",
            "provider": "github",
            "iat": now,
            "exp": now + 60,
        },
        secret_key(),
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(OAuthInvalidState):
        _decode_state(token, expected_provider="github")


def test_decode_state_rejects_expired_token() -> None:
    """A state past its `exp` must fail decode (jose enforces this)."""
    from core.config import secret_key
    from services.oauth_service import (
        STATE_TOKEN_TYPE,
        OAuthInvalidState,
        _decode_state,
    )

    # Hand-mint a JWT with exp 60s in the past — avoids a real-time sleep
    # (jose's leeway can reach a couple of seconds on slow CI hosts).
    now = int(time.time()) - 120
    token = jwt.encode(
        {
            "type": STATE_TOKEN_TYPE,
            "provider": "github",
            "iat": now,
            "exp": now + 1,  # 1s after iat → 119s in the past
        },
        secret_key(),
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(OAuthInvalidState):
        _decode_state(token, expected_provider="github")


# ---------------------------------------------------------------------------
# Personal team naming
# ---------------------------------------------------------------------------


def test_personal_team_name_carries_no_personal_data(  # ER32
) -> None:
    """The team name is on screen, so it must not be the person's name.

    It used to be ``{full_name}'s Team``, falling back to the email's local
    part. Both are personal data and both render in the team list, the
    switcher and every membership row, so an anonymised user would keep
    appearing there under their own name.
    """
    import uuid as _uuid

    from services.oauth_service import personal_team_name

    user_id = _uuid.UUID("3f1d8c2a-0000-0000-0000-00000000abcd")
    name = personal_team_name(user_id=user_id)

    assert name == "Personal team (3f1d8c2a0000)"
    assert "@" not in name


def test_personal_team_name_fits_the_column() -> None:
    """``teams.name`` is VARCHAR(255); an id-derived name cannot approach it."""
    import uuid as _uuid

    from services.oauth_service import personal_team_name

    assert len(personal_team_name(user_id=_uuid.uuid4())) <= 255


def test_personal_team_slug_is_deterministic_per_provider_user_id() -> None:
    from services.oauth_service import _personal_team_slug

    a = _personal_team_slug("github", "12345")
    b = _personal_team_slug("github", "12345")
    assert a == b
    assert a.startswith("github-")
    assert len(a) == len("github-") + 6


def test_personal_team_slug_distinguishes_providers() -> None:
    """Same provider_user_id under two providers MUST produce distinct slugs."""
    from services.oauth_service import _personal_team_slug

    gh = _personal_team_slug("github", "111")
    g = _personal_team_slug("google", "111")
    assert gh != g


# ---------------------------------------------------------------------------
# initiate_oauth — pure call, no DB
# ---------------------------------------------------------------------------


def test_initiate_oauth_returns_authorize_url_and_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.oauth_service import initiate_oauth

    monkeypatch.setenv("GITHUB_CLIENT_ID", "test-id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test-secret")

    url, state = initiate_oauth(
        provider="github",
        redirect_uri="https://app.test/auth/oauth/github/callback",
        redirect_after="/dashboard",
    )
    assert url.startswith("https://github.com/login/oauth/authorize")
    assert state in url
    # State must round-trip with redirect_after preserved.
    from services.oauth_service import _decode_state

    claims = _decode_state(state, expected_provider="github")
    assert claims["redirect_after"] == "/dashboard"


def test_initiate_oauth_unknown_provider_raises_404_class() -> None:
    from services.oauth_service import OAuthProviderUnknown, initiate_oauth

    with pytest.raises(OAuthProviderUnknown):
        initiate_oauth(
            provider="facebook",
            redirect_uri="https://x.test/cb",
            redirect_after=None,
        )


def test_initiate_oauth_unconfigured_raises_503_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.oauth_service import OAuthProviderUnavailable, initiate_oauth

    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)

    with pytest.raises(OAuthProviderUnavailable):
        initiate_oauth(
            provider="github",
            redirect_uri="https://x.test/cb",
            redirect_after=None,
        )


# ---------------------------------------------------------------------------
# Sanity check on iat / exp ranges
# ---------------------------------------------------------------------------


def test_signed_state_exp_matches_ttl_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.oauth_service import _decode_state, _signed_state

    monkeypatch.setenv("OAUTH_STATE_TTL_SECONDS", "120")

    before = int(datetime.now(tz=UTC).timestamp())
    state = _signed_state(provider="github", redirect_after=None)
    claims = _decode_state(state, expected_provider="github")

    # exp should be ~120s after iat with a small skew window.
    assert before + 120 - 5 <= claims["exp"] <= before + 120 + 5


# ---------------------------------------------------------------------------
# safe_redirect_after - A5 open-redirect guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate",
    [
        "https://evil.example",
        "https://evil.example/login",
        "http://evil.example",
        "//evil.example/steal",
        "/\\evil.example",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "evil.example/steal",
        "",
        "   ",
    ],
)
def test_safe_redirect_after_refuses_anything_off_site(candidate: str) -> None:
    """The value arrives on a public GET and ends up in a 302 with a cookie.

    `?redirect_after=https://evil.example` used to travel from the query
    string into the signed state and out of the callback verbatim, so a real
    sign-in delivered the user somewhere else. Nothing checked it: the router
    deferred to the service and the service deferred to the SPA.
    """
    from services.oauth_service import safe_redirect_after

    assert safe_redirect_after(candidate) is None


def test_safe_redirect_after_keeps_an_in_app_path() -> None:
    from services.oauth_service import safe_redirect_after

    assert safe_redirect_after("/projects/42") == "/projects/42"
    assert safe_redirect_after(None) is None


def test_safe_redirect_after_allows_the_deployment_front_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absolute URL is fine when it is our own front end, and only then."""
    from services import oauth_service

    monkeypatch.setenv("OAUTH_LOGIN_REDIRECT_DEFAULT", "https://portal.example")

    assert (
        oauth_service.safe_redirect_after("https://portal.example/projects")
        == "https://portal.example/projects"
    )
    # A different port, host or scheme is a different origin.
    assert oauth_service.safe_redirect_after("https://portal.example:8443/x") is None
    assert oauth_service.safe_redirect_after("http://portal.example/x") is None
    assert oauth_service.safe_redirect_after("https://portal.example.evil.com") is None


def test_safe_redirect_after_refuses_control_characters() -> None:
    from services.oauth_service import safe_redirect_after

    assert safe_redirect_after("/projects\r\nSet-Cookie: a=b") is None
    assert safe_redirect_after("/projects\n/x") is None


def test_initiate_oauth_does_not_sign_an_unsafe_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check runs before the state is minted, not only when it is read.

    A signature says we issued the token, not that its contents are safe, so
    the value must never get in. This is the assertion that would fail if
    someone moved the guard to the callback alone.
    """
    from services.oauth_service import _decode_state, initiate_oauth

    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "csecret")

    _url, state = initiate_oauth(
        provider="github",
        redirect_uri="https://portal.example/api/v1/auth/oauth/github/callback",
        redirect_after="https://evil.example",
    )

    claims = _decode_state(state, expected_provider="github")
    assert "redirect_after" not in claims
