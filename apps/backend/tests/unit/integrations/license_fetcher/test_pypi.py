"""
Unit tests for ``integrations.license_fetcher.pypi``.

PyPI's JSON endpoint exposes both ``info.classifiers`` (Trove) and the
free-text ``info.license``. We assert classifier mapping wins, free-text
maps through ``normalize_spdx_id`` as a fallback, and 404 / non-JSON
responses produce ``None``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from integrations.license_fetcher.pypi import (
    PyPILicenseFetcher,
    _parse_purl,
)

# ---------------------------------------------------------------------------
# PURL parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "purl,expected",
    [
        ("pkg:pypi/requests@2.32.3", ("requests", "2.32.3")),
        ("pkg:pypi/django@4.2.18", ("django", "4.2.18")),
        ("pkg:pypi/foo-bar@1.0.0", ("foo-bar", "1.0.0")),
        ("pkg:pypi/foo@1.0.0?ext=whl", ("foo", "1.0.0")),
    ],
)
def test_parse_purl_happy(purl: str, expected: tuple[str, str]) -> None:
    assert _parse_purl(purl) == expected


@pytest.mark.parametrize(
    "purl",
    ["pkg:maven/foo@1", "pkg:pypi/foo", "pkg:pypi/@1", "pkg:pypi/foo@"],
)
def test_parse_purl_rejects_malformed(purl: str) -> None:
    assert _parse_purl(purl) is None


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), timeout=1.0, follow_redirects=True
    )


def _info(**info: object) -> str:
    return json.dumps({"info": info})


def test_fetch_prefers_trove_classifier(no_throttle: None) -> None:
    payload = _info(
        license="See LICENSE",  # free-text would map to None — classifier wins
        classifiers=[
            "Development Status :: 5 - Production/Stable",
            "License :: OSI Approved :: Apache Software License",
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:pypi/foo@1.0.0")
    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert result.source == "pypi"


def test_fetch_falls_back_to_freetext_license(no_throttle: None) -> None:
    payload = _info(license="MIT License", classifiers=[])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:pypi/foo@1.0.0")
    assert result is not None
    assert result.spdx_id == "MIT"


def test_fetch_returns_none_when_neither_classifier_nor_freetext_maps(
    no_throttle: None,
) -> None:
    payload = _info(
        license="Proprietary, see internal wiki",
        classifiers=["Development Status :: 5 - Production/Stable"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:pypi/foo@1.0.0") is None


def test_fetch_returns_none_on_404(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:pypi/foo@1.0.0") is None


def test_fetch_returns_none_on_invalid_json(no_throttle: None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>")

    fetcher = PyPILicenseFetcher(http=_client(handler))
    assert fetcher.fetch("pkg:pypi/foo@1.0.0") is None


def test_fetch_url_shape_pin(no_throttle: None) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(404)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    fetcher.fetch("pkg:pypi/django@4.2.18")
    assert requested == ["https://pypi.org/pypi/django/4.2.18/json"]
