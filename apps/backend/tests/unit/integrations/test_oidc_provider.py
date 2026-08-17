"""
Generic OIDC provider: discovery, exchange and claim mapping.

The cases worth writing down are the refusals. This provider points at an
issuer an operator names, so everything it trusts arrives over the wire from a
URL in configuration, and each check below is a way that trust could be
misplaced: a document belonging to someone else, an endpoint that would carry
the code in the clear, a userinfo response with no verified address, a claim
under a name this deployment does not use.

Both HTTP clients are mocked. The provider fetches discovery synchronously
(the authorize path is a synchronous method) and everything else
asynchronously, so a test that patched only one would exercise half the
module and pass on the other half without calling it.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from integrations.oauth import get_provider
from integrations.oauth.base import OAuthExchangeError, OAuthProviderDisabled
from integrations.oauth.oidc import OidcProvider, discover, reset_discovery_cache

ISSUER = "https://idp.example.test"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZE_URL = f"{ISSUER}/authorize"
TOKEN_URL = f"{ISSUER}/token"
USERINFO_URL = f"{ISSUER}/userinfo"


def _discovery_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZE_URL,
        "token_endpoint": TOKEN_URL,
        "userinfo_endpoint": USERINFO_URL,
    }
    document.update(overrides)
    return document


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fully configured by default, so each test opts into a broken state."""
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", "client-id")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "client-secret")
    monkeypatch.delenv("OIDC_SCOPES", raising=False)
    monkeypatch.delenv("OIDC_EMAIL_CLAIM", raising=False)
    monkeypatch.delenv("OIDC_NAME_CLAIM", raising=False)
    monkeypatch.delenv("OIDC_REQUIRE_VERIFIED_EMAIL", raising=False)
    reset_discovery_cache()


@pytest.fixture
def route(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, object]], None]:
    """Serve canned responses to both the sync and the async client."""

    def install(responses: dict[str, object]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url not in responses:
                return httpx.Response(404, json={"error": "not_found"})
            canned = responses[url]
            if isinstance(canned, httpx.Response):
                return canned
            return httpx.Response(200, json=canned)

        for client_class in (httpx.Client, httpx.AsyncClient):
            real_init = client_class.__init__

            def _patched(self, *args, _real=real_init, **kwargs):  # type: ignore[no-untyped-def]
                kwargs["transport"] = (
                    httpx.MockTransport(handler)
                    if isinstance(self, httpx.Client)
                    else httpx.MockTransport(handler)
                )
                _real(self, *args, **kwargs)

            monkeypatch.setattr(client_class, "__init__", _patched)

    return install


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_the_provider_is_reachable_by_name() -> None:
    assert isinstance(get_provider("oidc"), OidcProvider)


def test_an_unconfigured_deployment_reports_the_provider_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    with pytest.raises(OAuthProviderDisabled):
        OidcProvider().authorize_url(state="s", redirect_uri="https://portal/callback")


def test_credentials_without_an_issuer_are_not_enough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is nowhere to send the browser, so this is disabled, not broken."""
    monkeypatch.setenv("OIDC_ISSUER", "")
    with pytest.raises(OAuthProviderDisabled):
        OidcProvider().authorize_url(state="s", redirect_uri="https://portal/callback")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_authorize_url_is_built_from_the_discovered_endpoint(route) -> None:
    route({DISCOVERY_URL: _discovery_document()})

    url = OidcProvider().authorize_url(state="state-token", redirect_uri="https://portal/cb")

    assert url.startswith(f"{AUTHORIZE_URL}?")
    assert "response_type=code" in url
    assert "state=state-token" in url
    assert "scope=openid+email+profile" in url


def test_a_document_claiming_a_different_issuer_is_refused(route) -> None:
    """The document says who it belongs to, and this one belongs elsewhere."""
    route({DISCOVERY_URL: _discovery_document(issuer="https://attacker.example.test")})

    with pytest.raises(OAuthExchangeError, match="issuer"):
        discover()


def test_a_plaintext_endpoint_is_refused(route) -> None:
    """It would carry the code and the token in the clear."""
    route({DISCOVERY_URL: _discovery_document(token_endpoint="http://idp.example.test/token")})

    with pytest.raises(OAuthExchangeError, match="non-HTTPS"):
        discover()


def test_a_document_missing_an_endpoint_is_refused(route) -> None:
    document = _discovery_document()
    del document["userinfo_endpoint"]
    route({DISCOVERY_URL: document})

    with pytest.raises(OAuthExchangeError, match="userinfo_endpoint"):
        discover()


def test_discovery_is_fetched_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_discovery_document())

    real_init = httpx.Client.__init__

    def _patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched)

    discover()
    discover()

    assert calls == [DISCOVERY_URL]


# ---------------------------------------------------------------------------
# Exchange and claims
# ---------------------------------------------------------------------------


async def test_a_successful_sign_in_maps_the_standard_claims(route) -> None:
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            TOKEN_URL: {"access_token": "at"},
            USERINFO_URL: {
                "sub": "subject-1",
                "email": "Person@Example.test",
                "email_verified": True,
                "name": "A Person",
                "picture": "https://idp.example.test/a.png",
            },
        }
    )
    provider = OidcProvider()

    token = await provider.exchange_code_for_token(code="c", redirect_uri="https://portal/cb")
    info = await provider.fetch_user_info(access_token=token)

    assert token == "at"
    assert info.provider == "oidc"
    assert info.provider_user_id == "subject-1"
    assert info.email == "person@example.test"
    assert info.full_name == "A Person"


async def test_an_unverified_address_is_refused(route) -> None:
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            USERINFO_URL: {"sub": "s", "email": "p@example.test", "email_verified": False},
        }
    )

    with pytest.raises(OAuthExchangeError, match="verified"):
        await OidcProvider().fetch_user_info(access_token="at")


async def test_a_missing_verification_claim_counts_as_unverified(route) -> None:
    """Silence is not consent: a provider that does not say has not verified."""
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            USERINFO_URL: {"sub": "s", "email": "p@example.test"},
        }
    )

    with pytest.raises(OAuthExchangeError, match="verified"):
        await OidcProvider().fetch_user_info(access_token="at")


async def test_the_verification_requirement_can_be_turned_off_deliberately(
    route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OIDC_REQUIRE_VERIFIED_EMAIL", "false")
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            USERINFO_URL: {"sub": "s", "email": "p@example.test"},
        }
    )

    info = await OidcProvider().fetch_user_info(access_token="at")

    assert info.email == "p@example.test"


async def test_the_address_can_come_from_a_provider_specific_claim(
    route, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OIDC_EMAIL_CLAIM", "preferred_username")
    monkeypatch.setenv("OIDC_REQUIRE_VERIFIED_EMAIL", "false")
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            USERINFO_URL: {"sub": "s", "preferred_username": "p@example.test"},
        }
    )

    info = await OidcProvider().fetch_user_info(access_token="at")

    assert info.email == "p@example.test"


async def test_a_response_without_a_subject_is_refused(route) -> None:
    """Without a stable id there is nothing to bind the account to."""
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            USERINFO_URL: {"email": "p@example.test", "email_verified": True},
        }
    )

    with pytest.raises(OAuthExchangeError, match="sub"):
        await OidcProvider().fetch_user_info(access_token="at")


async def test_a_provider_error_on_exchange_is_surfaced(route) -> None:
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            TOKEN_URL: {"error": "invalid_grant"},
        }
    )

    with pytest.raises(OAuthExchangeError, match="invalid_grant"):
        await OidcProvider().exchange_code_for_token(code="c", redirect_uri="https://portal/cb")


async def test_a_non_json_userinfo_body_is_refused(route) -> None:
    route(
        {
            DISCOVERY_URL: _discovery_document(),
            USERINFO_URL: httpx.Response(200, content=b"<html>not json</html>"),
        }
    )

    with pytest.raises(OAuthExchangeError, match="non-JSON"):
        await OidcProvider().fetch_user_info(access_token="at")


def test_the_secret_never_reaches_the_authorize_url(route) -> None:
    route({DISCOVERY_URL: _discovery_document()})

    url = OidcProvider().authorize_url(state="s", redirect_uri="https://portal/cb")

    assert "client-secret" not in url
    assert json.dumps({"url": url}).count("client-secret") == 0
