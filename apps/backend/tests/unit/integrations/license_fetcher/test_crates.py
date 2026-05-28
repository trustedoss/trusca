"""
Unit tests for ``integrations.license_fetcher.crates``.

crates.io v1 returns ``version.license`` as an SPDX expression
directly. We assert single-id values pass through, compound
expressions fall to ``None`` (delegated to ``normalize_spdx_id``),
and 404 / shape errors return ``None``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from integrations.license_fetcher.crates import (
    CratesLicenseFetcher,
    _parse_purl,
)


@pytest.mark.parametrize(
    "purl,expected",
    [
        ("pkg:cargo/serde@1.0.219", ("serde", "1.0.219")),
        ("pkg:cargo/tokio_util@0.7.13", ("tokio_util", "0.7.13")),
        ("pkg:cargo/foo@1.0.0?bar=baz", ("foo", "1.0.0")),
    ],
)
def test_parse_purl_happy(purl: str, expected: tuple[str, str]) -> None:
    assert _parse_purl(purl) == expected


@pytest.mark.parametrize(
    "purl",
    ["pkg:pypi/foo@1", "pkg:cargo/foo", "pkg:cargo/foo@", "pkg:cargo/@1"],
)
def test_parse_purl_rejects_malformed(purl: str) -> None:
    assert _parse_purl(purl) is None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), timeout=1.0, follow_redirects=True
    )


def test_fetch_returns_single_spdx_license(no_throttle: None) -> None:
    payload = json.dumps({"version": {"license": "Apache-2.0"}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = CratesLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:cargo/serde@1.0.219")
    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert result.source == "crates_io"


def test_fetch_resolves_or_compound_expression_to_first_token(
    no_throttle: None,
) -> None:
    """crates.io exposes ``MIT OR Apache-2.0`` for the dominant Rust
    dual-licensing convention. chore PR #7 (UAT v2 fix) updated
    ``normalize_spdx_id`` to pick the first valid token — picking
    either token is sound per SPDX OR semantics.
    """
    payload = json.dumps({"version": {"license": "MIT OR Apache-2.0"}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = CratesLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:cargo/serde@1.0.219")
    assert result is not None
    assert result.spdx_id == "MIT"
    assert result.source == "crates_io"


def test_fetch_returns_none_on_and_compound_expression(no_throttle: None) -> None:
    """``AND`` compounds still resolve to None — picking a single SPDX
    id when the licence requires both is unsound.
    """
    payload = json.dumps({"version": {"license": "BSD-3-Clause AND MIT"}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = CratesLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:cargo/serde@1.0.219") is None


def test_fetch_returns_none_on_missing_license_field(no_throttle: None) -> None:
    payload = json.dumps({"version": {"license": None}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = CratesLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:cargo/serde@1.0.219") is None


def test_fetch_returns_none_on_404(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = CratesLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:cargo/serde@1.0.219") is None


def test_fetch_url_shape_pin(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404)

    fetcher = CratesLicenseFetcher(http=_client(handler))
    fetcher.fetch("pkg:cargo/serde@1.0.219")
    assert requested == ["https://crates.io/api/v1/crates/serde/1.0.219"]


def test_fetch_handles_429_with_retry(no_throttle: None) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429)
        return httpx.Response(200, text=json.dumps({"version": {"license": "MIT"}}))

    fetcher = CratesLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:cargo/serde@1.0.219")
    assert result is not None
    assert result.spdx_id == "MIT"
    assert len(attempts) == 2


# ---------------------------------------------------------------------------
# follow_redirects=False — security-reviewer L4 (chore PR #6)
# ---------------------------------------------------------------------------


def _client_no_redirects(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        timeout=1.0,
        follow_redirects=False,
    )


def test_fetch_does_not_follow_redirect_to_attacker_host(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            307,
            headers={"Location": "https://attacker.example/api/v1/crates/foo/1"},
        )

    fetcher = CratesLicenseFetcher(http=_client_no_redirects(handler))
    result = fetcher.fetch("pkg:cargo/foo@1.0.0")

    assert result is None
    assert len(requested) == 1
    assert "attacker.example" not in requested[0]


def test_default_client_disables_redirect_following() -> None:
    fetcher = CratesLicenseFetcher()
    client = fetcher._client(timeout=1.0)
    try:
        assert client.follow_redirects is False
    finally:
        fetcher.close()
