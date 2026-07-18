"""
Unit tests for ``integrations.license_fetcher.rubygems`` (W8-#49).

RubyGems v2 returns a ``licenses`` array of SPDX-ish strings. We assert a
single id passes through, the first mappable entry wins, an empty/missing
array → None, and 404 / shape errors → None. Network is mocked via
``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from integrations.license_fetcher.rubygems import RubyGemsLicenseFetcher, _parse_purl


@pytest.mark.parametrize(
    "purl,expected",
    [
        ("pkg:gem/rails@7.1.0", ("rails", "7.1.0")),
        ("pkg:gem/nokogiri@1.16.0?arch=x86", ("nokogiri", "1.16.0")),
    ],
)
def test_parse_purl_happy(purl: str, expected: tuple[str, str]) -> None:
    assert _parse_purl(purl) == expected


@pytest.mark.parametrize(
    "purl", ["pkg:pypi/foo@1", "pkg:gem/foo", "pkg:gem/foo@", "pkg:gem/@1"]
)
def test_parse_purl_rejects_malformed(purl: str) -> None:
    assert _parse_purl(purl) is None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), timeout=1.0, follow_redirects=True
    )


def test_fetch_returns_single_license(no_throttle: None) -> None:
    payload = json.dumps({"licenses": ["MIT"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = RubyGemsLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:gem/rails@7.1.0")
    assert result is not None
    assert result.spdx_id == "MIT"
    assert result.source == "rubygems"


def test_fetch_takes_first_mappable_of_several(no_throttle: None) -> None:
    # First entry is unmappable free-text; the second is a clean SPDX id.
    payload = json.dumps({"licenses": ["Nonstandard Custom Terms", "Apache-2.0"]})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = RubyGemsLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:gem/rails@7.1.0")
    assert result is not None
    assert result.spdx_id == "Apache-2.0"


@pytest.mark.parametrize("licenses", [None, [], ["  "], "MIT"])
def test_fetch_returns_none_on_absent_or_unmappable(
    no_throttle: None, licenses: object
) -> None:
    payload = json.dumps({"licenses": licenses})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = RubyGemsLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:gem/rails@7.1.0") is None


def test_fetch_returns_none_on_404(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = RubyGemsLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:gem/rails@7.1.0") is None


def test_fetch_url_shape_pin(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404)

    fetcher = RubyGemsLicenseFetcher(http=_client(handler))
    fetcher.fetch("pkg:gem/rails@7.1.0")
    assert requested == [
        "https://rubygems.org/api/v2/rubygems/rails/versions/7.1.0.json"
    ]


def test_hostile_name_cannot_escape_registry_path(no_throttle: None) -> None:
    """A slash-bearing gem name is percent-encoded (safe=''), so it stays a
    single path segment on rubygems.org — no in-registry path traversal."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404)

    fetcher = RubyGemsLicenseFetcher(http=_client(handler))
    fetcher.fetch("pkg:gem/..%2f..%2fevil@1.0.0")
    assert len(requested) == 1
    assert requested[0].startswith("https://rubygems.org/api/v2/rubygems/")
    # The encoded traversal is escaped again, never a bare '/../'.
    assert "/../" not in requested[0]
