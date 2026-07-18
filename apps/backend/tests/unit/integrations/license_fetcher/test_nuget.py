"""
Unit tests for ``integrations.license_fetcher.nuget`` (W8-#49).

NuGet's registration leaf carries a ``catalogEntry`` — inline object or a URL
to fetch — whose ``licenseExpression`` is an SPDX expression. We assert the
inline and URL-hop shapes both resolve, an off-host catalog URL is refused,
a legacy licenseUrl-only package → None, and 404 → None. Network is mocked.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from integrations.license_fetcher.nuget import NuGetLicenseFetcher, _parse_purl


@pytest.mark.parametrize(
    "purl,expected",
    [
        ("pkg:nuget/Newtonsoft.Json@13.0.1", ("Newtonsoft.Json", "13.0.1")),
        ("pkg:nuget/Serilog@3.1.1?x=y", ("Serilog", "3.1.1")),
    ],
)
def test_parse_purl_happy(purl: str, expected: tuple[str, str]) -> None:
    assert _parse_purl(purl) == expected


@pytest.mark.parametrize(
    "purl", ["pkg:pypi/foo@1", "pkg:nuget/foo", "pkg:nuget/foo@", "pkg:nuget/@1"]
)
def test_parse_purl_rejects_malformed(purl: str) -> None:
    assert _parse_purl(purl) is None


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), timeout=1.0, follow_redirects=True
    )


def test_fetch_inline_catalog_entry(no_throttle: None) -> None:
    payload = json.dumps(
        {"catalogEntry": {"licenseExpression": "MIT"}}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = NuGetLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:nuget/Newtonsoft.Json@13.0.1")
    assert result is not None
    assert result.spdx_id == "MIT"
    assert result.source == "nuget"


def test_fetch_follows_catalog_url_hop(no_throttle: None) -> None:
    catalog_url = "https://api.nuget.org/v3/catalog0/data/x/newtonsoft.json.13.0.1.json"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("13.0.1.json") and "catalog0" not in str(request.url):
            return httpx.Response(200, text=json.dumps({"catalogEntry": catalog_url}))
        return httpx.Response(200, text=json.dumps({"licenseExpression": "Apache-2.0"}))

    fetcher = NuGetLicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:nuget/Newtonsoft.Json@13.0.1")
    assert result is not None
    assert result.spdx_id == "Apache-2.0"


def test_fetch_refuses_off_host_catalog_url(no_throttle: None) -> None:
    # A catalogEntry pointing off api.nuget.org must not be followed.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=json.dumps({"catalogEntry": "https://evil.example.com/x.json"}),
        )

    fetcher = NuGetLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:nuget/Newtonsoft.Json@13.0.1") is None


def test_fetch_returns_none_when_only_license_url(no_throttle: None) -> None:
    # Legacy package: licenseUrl (a link) but no SPDX licenseExpression.
    payload = json.dumps(
        {"catalogEntry": {"licenseUrl": "https://licenses.nuget.org/MIT"}}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = NuGetLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:nuget/Old.Package@1.0.0") is None


def test_fetch_returns_none_on_404(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = NuGetLicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:nuget/Newtonsoft.Json@13.0.1") is None


def test_fetch_url_is_lowercased(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404)

    fetcher = NuGetLicenseFetcher(http=_client(handler))
    fetcher.fetch("pkg:nuget/Newtonsoft.Json@13.0.1")
    assert requested == [
        "https://api.nuget.org/v3/registration5-semver1/newtonsoft.json/13.0.1.json"
    ]


def test_hostile_id_cannot_escape_registry_path(no_throttle: None) -> None:
    """A slash-bearing NuGet id is percent-encoded (safe=''), so it stays a
    single path segment under api.nuget.org — no in-registry path traversal."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404)

    fetcher = NuGetLicenseFetcher(http=_client(handler))
    fetcher.fetch("pkg:nuget/..%2f..%2fetc@1.0.0")
    assert len(requested) == 1
    assert requested[0].startswith(
        "https://api.nuget.org/v3/registration5-semver1/"
    )
    assert "/../" not in requested[0]
