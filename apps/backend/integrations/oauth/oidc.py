# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Generic OpenID Connect provider: the deployment's own identity provider.

The two providers beside this one are named services with pinned endpoints.
This one is named by configuration: an operator sets the issuer and the client
credentials, and every endpoint comes from the issuer's discovery document.
That is what makes it generic, and it is also why it is a single provider
rather than a configurable list. An organisation has one identity provider,
and supporting several at once would widen the provider name from a closed set
into user input for no case anyone has.

Deliberately no token signature verification here. The provider returns the
authorisation code over the browser redirect, and this module exchanges it for
an access token over TLS directly with the issuer's token endpoint, then reads
the subject and claims from the userinfo endpoint over the same channel. That
is the same shape as the other two providers. Validating an ID token instead
would mean fetching a key set, matching a key id, checking a signature,
issuer, audience and expiry by hand, and getting any of those subtly wrong is
the classic way an authentication path fails open. There is nothing to gain
here: the code never leaves the exchange, so there is no second-hand token to
authenticate.

Discovery is cached per issuer with a short lifetime. Without a cache every
sign-in would pay two round-trips before the browser is even redirected.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import structlog

from core.config import (
    oauth_http_timeout_seconds,
    oidc_client_id,
    oidc_client_secret,
    oidc_issuer,
    oidc_scopes,
)

from .base import (
    OAUTH_PROVIDER_OIDC,
    OAuthExchangeError,
    OAuthProviderDisabled,
    OAuthUserInfo,
)

log = structlog.get_logger("integrations.oauth.oidc")

DISCOVERY_PATH = "/.well-known/openid-configuration"

#: How long a discovery document is reused. Issuers change endpoints rarely,
#: and an hour bounds how long a stale document can be served after one does.
DISCOVERY_TTL_SECONDS = 3600

#: Timeout for the discovery request alone, shorter than the exchange timeout.
#: This request happens inline on a public endpoint and blocks the event loop
#: for its duration, so the ceiling on one stall is this number, not the ten
#: seconds an exchange with a slow provider is allowed to take.
DISCOVERY_TIMEOUT_SECONDS = 3

#: How long a failure is remembered. Without this, an issuer that is down turns
#: every sign-in attempt into another blocking round-trip against it, and the
#: endpoint is public and unauthenticated, so the cost is one an anonymous
#: caller chooses. Short enough that recovery is not delayed noticeably.
DISCOVERY_FAILURE_TTL_SECONDS = 30


@dataclass(frozen=True)
class _Endpoints:
    authorize: str
    token: str
    userinfo: str


_cache: dict[str, tuple[float, _Endpoints]] = {}
_failures: dict[str, tuple[float, str]] = {}

#: One fetch at a time per process. Concurrent sign-ins on a cold cache would
#: otherwise each open their own request and each block for the full timeout.
_lock = threading.Lock()


def _require_credentials() -> tuple[str, str, str]:
    issuer = oidc_issuer()
    client_id = oidc_client_id()
    client_secret = oidc_client_secret()
    if not issuer or not client_id or not client_secret:
        raise OAuthProviderDisabled("OIDC is not configured on this deployment")
    if urlsplit(issuer).scheme != "https":
        # The discovery request decides every other endpoint, so it is the one
        # request that must not be tamperable. Refusing plaintext at the
        # endpoints while fetching the document that names them over plaintext
        # would guard the door and leave the hinges.
        raise OAuthProviderDisabled("OIDC_ISSUER must be an https URL")
    return issuer, client_id, client_secret


def _endpoint(document: dict[str, Any], key: str, issuer: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise OAuthExchangeError(f"discovery document for {issuer} has no usable {key}")

    parsed = urlsplit(value)
    if parsed.scheme != "https":
        # An http endpoint would carry the code and the access token in the
        # clear. Refusing is the only safe reading, and a provider serving one
        # is misconfigured rather than unusual.
        raise OAuthExchangeError(f"discovery document for {issuer} advertises a non-HTTPS {key}")

    # And it has to be the issuer's own host. Without this the document can
    # send the exchange anywhere: a token endpoint on another host receives
    # the client secret and a live authorisation code, and a userinfo endpoint
    # there can claim any subject and address it likes, which the sign-in then
    # binds to an existing account. Checking the scheme alone leaves the trust
    # in the document rather than in the issuer the operator named.
    if parsed.netloc != urlsplit(issuer).netloc:
        log.warning("oauth_oidc_endpoint_off_issuer", key=key, host=parsed.netloc)
        raise OAuthExchangeError(
            f"discovery document for {issuer} points {key} at a different host"
        )
    return value


def discover(*, force: bool = False) -> _Endpoints:
    """Return the issuer's endpoints, fetching the discovery document if needed.

    Synchronous on purpose: ``authorize_url`` is a synchronous method on the
    provider protocol, and one code path for the document is worth more than
    saving a blocking call that happens once per cache lifetime.
    """
    issuer, _, _ = _require_credentials()
    now = time.monotonic()
    cached = _cache.get(issuer)
    if cached and not force and now - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]

    with _lock:
        # Another caller may have finished while this one waited.
        cached = _cache.get(issuer)
        if cached and not force and time.monotonic() - cached[0] < DISCOVERY_TTL_SECONDS:
            return cached[1]
        failed = _failures.get(issuer)
        if failed and time.monotonic() - failed[0] < DISCOVERY_FAILURE_TTL_SECONDS:
            raise OAuthExchangeError(failed[1])
        return _fetch(issuer)


def _fetch(issuer: str) -> _Endpoints:
    """Fetch and validate the document. Callers hold ``_lock``."""

    def remember_failure(message: str) -> OAuthExchangeError:
        _failures[issuer] = (time.monotonic(), message)
        return OAuthExchangeError(message)

    now = time.monotonic()
    url = f"{issuer}{DISCOVERY_PATH}"
    try:
        with httpx.Client(timeout=DISCOVERY_TIMEOUT_SECONDS) as client:
            response = client.get(url, headers={"Accept": "application/json"})
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # Broader than timeout and network: a misspelled issuer raises
        # UnsupportedProtocol, a proxy failure raises ProxyError, and each of
        # those would otherwise leave this endpoint answering 500 with a
        # traceback instead of the documented failure.
        log.warning("oauth_oidc_discovery_network_failure", error_type=type(exc).__name__)
        raise remember_failure("oidc discovery network failure") from exc

    if response.status_code != 200:
        log.warning("oauth_oidc_discovery_http_error", status=response.status_code)
        raise remember_failure(f"oidc discovery returned HTTP {response.status_code}")

    try:
        document = response.json()
    except ValueError as exc:
        raise remember_failure("oidc discovery returned non-JSON body") from exc
    if not isinstance(document, dict):
        raise remember_failure("oidc discovery returned a non-object body")

    advertised = document.get("issuer")
    if advertised != issuer:
        # The document says who it belongs to. A mismatch means the configured
        # URL is not the issuer it claims to be, which is exactly the case a
        # sign-in must not proceed through.
        log.warning("oauth_oidc_discovery_issuer_mismatch", configured=issuer)
        raise remember_failure("oidc discovery issuer does not match the configured issuer")

    try:
        endpoints = _Endpoints(
            authorize=_endpoint(document, "authorization_endpoint", issuer),
            token=_endpoint(document, "token_endpoint", issuer),
            userinfo=_endpoint(document, "userinfo_endpoint", issuer),
        )
    except OAuthExchangeError as exc:
        # A document that answers quickly but does not validate is still a
        # failure worth remembering. Without this the fetch repeats on every
        # sign-in attempt, which is the amplification the failure cache exists
        # to stop, just reached through the validation path instead.
        raise remember_failure(str(exc)) from None
    _cache[issuer] = (now, endpoints)
    _failures.pop(issuer, None)
    return endpoints


def reset_discovery_cache() -> None:
    """Drop the cached documents and failures. Used by tests and after a config change."""
    _cache.clear()
    _failures.clear()


class OidcProvider:
    """Implements :class:`integrations.oauth.base.OAuthProvider` generically."""

    name = OAUTH_PROVIDER_OIDC

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        _, client_id, _ = _require_credentials()
        endpoints = discover()
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": oidc_scopes(),
            "state": state,
        }
        return f"{endpoints.authorize}?{urlencode(params)}"

    async def exchange_code_for_token(self, *, code: str, redirect_uri: str) -> str:
        _, client_id, client_secret = _require_credentials()
        endpoints = discover()
        try:
            async with httpx.AsyncClient(timeout=oauth_http_timeout_seconds()) as client:
                response = await client.post(
                    endpoints.token,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "code": code,
                        "grant_type": "authorization_code",
                        "redirect_uri": redirect_uri,
                    },
                )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log.warning("oauth_oidc_exchange_network_failure", error_type=type(exc).__name__)
            raise OAuthExchangeError("oidc token exchange network failure") from exc

        if response.status_code != 200:
            log.warning("oauth_oidc_exchange_http_error", status=response.status_code)
            raise OAuthExchangeError(f"oidc token exchange returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthExchangeError("oidc token exchange returned non-JSON body") from exc
        if not isinstance(payload, dict):
            raise OAuthExchangeError("oidc token exchange returned a non-object body")
        if "error" in payload:
            log.warning("oauth_oidc_exchange_provider_error", error_code=payload.get("error"))
            raise OAuthExchangeError(f"oidc exchange error: {payload.get('error')!s}")

        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise OAuthExchangeError("oidc exchange returned no access_token")
        return access_token

    async def fetch_user_info(self, *, access_token: str) -> OAuthUserInfo:
        endpoints = discover()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=oauth_http_timeout_seconds(), headers=headers
            ) as client:
                response = await client.get(endpoints.userinfo)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            log.warning("oauth_oidc_userinfo_network_failure", error_type=type(exc).__name__)
            raise OAuthExchangeError("oidc userinfo network failure") from exc

        if response.status_code != 200:
            log.warning("oauth_oidc_userinfo_http_error", status=response.status_code)
            raise OAuthExchangeError(f"oidc userinfo returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise OAuthExchangeError("oidc userinfo returned non-JSON body") from exc
        if not isinstance(payload, dict):
            raise OAuthExchangeError("oidc userinfo returned a non-object body")

        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OAuthExchangeError("oidc userinfo missing 'sub' claim")

        # The standard claim, and only it. ``email_verified`` vouches for
        # ``email`` and nothing else (OIDC Core 5.1), so an address read from
        # elsewhere carries a verification flag that does not describe it, and
        # on several providers that other claim is user-editable. A deployment
        # whose provider puts the address elsewhere maps it to ``email`` in the
        # provider, which is where claim mapping belongs.
        email = payload.get("email")
        if not isinstance(email, str) or not email.strip():
            raise OAuthExchangeError("oidc userinfo missing 'email' claim")

        if payload.get("email_verified") is not True:
            # Absent counts as unverified: a provider that does not say has not
            # verified anything as far as this deployment can tell.
            log.warning("oauth_oidc_email_unverified", sub=subject)
            raise OAuthExchangeError("oidc userinfo does not report a verified email")

        full_name = payload.get("name")
        if not isinstance(full_name, str) or not full_name.strip():
            full_name = None

        avatar_url = payload.get("picture")
        if not isinstance(avatar_url, str) or not avatar_url:
            avatar_url = None

        return OAuthUserInfo(
            provider=OAUTH_PROVIDER_OIDC,
            provider_user_id=subject,
            email=email.strip().lower(),
            full_name=full_name,
            avatar_url=avatar_url,
            email_can_link_existing_account=True,
        )


__all__ = [
    "DISCOVERY_PATH",
    "DISCOVERY_TTL_SECONDS",
    "OidcProvider",
    "discover",
    "reset_discovery_cache",
]
