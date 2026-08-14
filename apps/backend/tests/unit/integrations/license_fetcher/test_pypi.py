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


# ---------------------------------------------------------------------------
# follow_redirects=False — security review finding
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
            301,
            headers={"Location": "https://attacker.example/json"},
        )

    fetcher = PyPILicenseFetcher(http=_client_no_redirects(handler))
    result = fetcher.fetch("pkg:pypi/foo@1.0.0")

    assert result is None
    assert len(requested) == 1
    assert "attacker.example" not in requested[0]


def test_default_client_disables_redirect_following() -> None:
    fetcher = PyPILicenseFetcher()
    client = fetcher._client(timeout=1.0)
    try:
        assert client.follow_redirects is False
    finally:
        fetcher.close()


# ---------------------------------------------------------------------------
# Multi-licensed packages (#86)
# ---------------------------------------------------------------------------


def test_fetch_joins_every_matching_classifier(no_throttle: None) -> None:
    """Three licence classifiers are an offer, not a list to pick from.

    These are pyphen 0.17.2's actual classifiers, read from its wheel. The
    self-scan recorded it as GPL-2.0-or-later alone and the build gate failed
    on it; the package is takeable under LGPL or MPL, which the OR expression
    lets the evaluator see.
    """
    payload = _info(
        classifiers=[
            "Development Status :: 5 - Production/Stable",
            "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
            "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)",
            "License :: OSI Approved :: Mozilla Public License 1.1 (MPL 1.1)",
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:pypi/pyphen@0.17.2")
    assert result is not None
    assert " OR " in result.spdx_id
    # LGPLv2+ maps to LGPL-2.0-or-later: the Trove classifier says "v2 or
    # later", and 2.1 has its own classifier.
    assert result.spdx_id.split(" OR ") == [
        "GPL-2.0-or-later",
        "LGPL-2.0-or-later",
        "MPL-1.1",
    ]


def test_multi_classifier_result_classifies_as_conditional() -> None:
    """The point of the join: the expression stops reading as forbidden.

    Asserted through the evaluator the scan pipeline actually uses, so this
    fails if either the join or the OR semantics regress.
    """
    from tasks.scan_source import _classify_license_category

    assert _classify_license_category("GPL-2.0-or-later") == "forbidden"
    assert (
        _classify_license_category("GPL-2.0-or-later OR LGPL-2.0-or-later OR MPL-1.1")
        == "conditional"
    )


def test_fetch_keeps_a_single_classifier_unjoined(no_throttle: None) -> None:
    """One classifier still yields a bare id, not a one-term expression."""
    payload = _info(
        classifiers=["License :: OSI Approved :: MIT License"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:pypi/foo@1.0.0")
    assert result is not None
    assert result.spdx_id == "MIT"


def test_fetch_drops_duplicate_classifier_mappings(no_throttle: None) -> None:
    """Two classifiers that map to the same id are one licence, not a choice."""
    payload = _info(
        classifiers=[
            "License :: OSI Approved :: MIT License",
            "License :: OSI Approved :: MIT No Attribution License (MIT-0)",
            "License :: OSI Approved :: MIT License",
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=payload)

    fetcher = PyPILicenseFetcher(http=_client(handler))
    result = fetcher.fetch("pkg:pypi/foo@1.0.0")
    assert result is not None
    assert result.spdx_id.count("MIT ") <= 1 or result.spdx_id == "MIT"
    assert result.spdx_id.split(" OR ") == list(dict.fromkeys(result.spdx_id.split(" OR ")))
