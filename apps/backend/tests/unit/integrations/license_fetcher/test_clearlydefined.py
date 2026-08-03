# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
ClearlyDefined fallback fetcher — unit tests (S5-A).

Driven by responses captured from the live API (see
``tests/fixtures/clearlydefined/README.md``) rather than hand-written JSON.
The density is the point: the lodash capture carries a compound
``CC0-1.0 AND MIT`` declaration and three distinct copyright holders, which is
the exact shape a synthetic fixture would have flattened into "one licence, one
holder" — and it is the shape that shows attributions surviving a licence the
normaliser refuses.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from integrations.license_fetcher.clearlydefined import (
    ClearlyDefinedLicenseFetcher,
    extract_attributions,
    extract_declared_license,
    parse_purl,
)

FIXTURES = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "clearlydefined"
LODASH = json.loads(
    (FIXTURES / "real-npm-npmjs---lodash-4.17.21.json").read_text(encoding="utf-8")
)
REQUESTS = json.loads(
    (FIXTURES / "real-pypi-pypi---requests-2.31.0.json").read_text(encoding="utf-8")
)


# ---------------------------------------------------------------------------
# purl → coordinates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("purl", "expected"),
    [
        ("pkg:npm/lodash@4.17.21", ("npm", "-", "lodash", "4.17.21")),
        ("pkg:npm/@babel/core@7.24.0", ("npm", "@babel", "core", "7.24.0")),
        ("pkg:pypi/requests@2.31.0", ("pypi", "-", "requests", "2.31.0")),
        (
            "pkg:maven/org.slf4j/slf4j-api@2.0.9",
            ("maven", "org.slf4j", "slf4j-api", "2.0.9"),
        ),
        ("pkg:cargo/serde@1.0.197", ("cargo", "-", "serde", "1.0.197")),
        # Qualifiers and subpaths carry no coordinate information.
        ("pkg:npm/lodash@4.17.21?arch=x64#sub/path", ("npm", "-", "lodash", "4.17.21")),
    ],
)
def test_parse_purl_maps_supported_types(purl: str, expected: tuple) -> None:
    assert parse_purl(purl) == expected


@pytest.mark.parametrize(
    "purl",
    [
        "",
        "not-a-purl",
        "pkg:npm/lodash",  # no revision — nothing to ask for
        "pkg:npm/lodash@",  # empty revision
        "pkg:conan/zlib@1.3",  # type with no coordinate mapping
        "pkg:npm/",
    ],
)
def test_parse_purl_declines_rather_than_guessing(purl: str) -> None:
    """A wrong coordinate returns someone else's licence, so we return nothing."""
    assert parse_purl(purl) is None


# ---------------------------------------------------------------------------
# Payload extraction, against captured responses
# ---------------------------------------------------------------------------


def test_declared_license_from_a_clean_single_id() -> None:
    assert extract_declared_license(REQUESTS) == "Apache-2.0"


def test_a_compound_declaration_yields_no_single_id() -> None:
    """`CC0-1.0 AND MIT` means both apply, which one SPDX id cannot express.

    The shared normaliser's policy, not this adapter's — asserted here so the
    lodash fixture's real shape is pinned rather than assumed.
    """
    assert LODASH["licensed"]["declared"] == "CC0-1.0 AND MIT"
    assert extract_declared_license(LODASH) is None


def test_noassertion_is_not_a_license() -> None:
    payload = {"licensed": {"declared": "NOASSERTION"}}
    assert extract_declared_license(payload) is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"licensed": None},
        {"licensed": {}},
        {"licensed": {"declared": ""}},
        {"licensed": {"declared": 42}},
    ],
)
def test_malformed_payloads_are_survivable(payload: dict) -> None:
    assert extract_declared_license(payload) is None
    assert extract_attributions(payload) == []


def test_attributions_come_back_from_a_real_capture() -> None:
    parties = extract_attributions(LODASH)
    assert len(parties) == 3
    assert any("OpenJS Foundation" in party for party in parties)


def test_attributions_survive_a_license_the_normaliser_refuses() -> None:
    """The point of capturing them before the licence check.

    lodash declares a compound expression, so the fetcher returns no licence —
    but its copyright holders are exactly what the NOTICE is short of.
    """
    assert extract_declared_license(LODASH) is None
    assert extract_attributions(LODASH)


def test_attributions_are_deduplicated_and_capped() -> None:
    payload = {
        "licensed": {
            "facets": {
                "core": {
                    "attribution": {
                        "parties": ["A", "A", " A ", *[f"H{i}" for i in range(30)]]
                    }
                }
            }
        }
    }
    parties = extract_attributions(payload, limit=5)
    assert parties[0] == "A"
    assert len(parties) == 5
    assert len(set(parties)) == 5


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


def test_fetch_returns_the_declared_license(make_mock_client, no_throttle) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/definitions/pypi/pypi/-/requests/2.31.0"
        return httpx.Response(200, json=REQUESTS)

    fetcher = ClearlyDefinedLicenseFetcher(http=make_mock_client(handler))
    result = fetcher.fetch("pkg:pypi/requests@2.31.0")
    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert result.source == "clearlydefined"
    assert len(fetcher.last_attributions) == 4


def test_fetch_keeps_attributions_when_the_license_is_unusable(
    make_mock_client, no_throttle
) -> None:
    fetcher = ClearlyDefinedLicenseFetcher(
        http=make_mock_client(lambda request: httpx.Response(200, json=LODASH))
    )
    assert fetcher.fetch("pkg:npm/lodash@4.17.21") is None
    assert len(fetcher.last_attributions) == 3


def test_fetch_declines_an_unmapped_purl_without_a_request(
    make_mock_client, no_throttle
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={})

    fetcher = ClearlyDefinedLicenseFetcher(http=make_mock_client(handler))
    assert fetcher.fetch("pkg:conan/zlib@1.3") is None
    assert calls == []


def test_fetch_survives_a_404(make_mock_client, no_throttle) -> None:
    fetcher = ClearlyDefinedLicenseFetcher(
        http=make_mock_client(lambda request: httpx.Response(404))
    )
    assert fetcher.fetch("pkg:npm/nope@1.0.0") is None
    assert fetcher.last_attributions == []


def test_fetch_survives_malformed_json(make_mock_client, no_throttle) -> None:
    fetcher = ClearlyDefinedLicenseFetcher(
        http=make_mock_client(lambda request: httpx.Response(200, text="{oh no"))
    )
    assert fetcher.fetch("pkg:npm/lodash@4.17.21") is None


def test_a_scoped_npm_name_escapes_into_one_path_segment(
    make_mock_client, no_throttle
) -> None:
    """`@babel` must not invent an extra path element.

    Asserted on the raw (still percent-encoded) path — `url.path` hands back a
    decoded string, which would show `@babel` and pass whether or not the
    escaping happened.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode("ascii"))
        return httpx.Response(200, json=REQUESTS)

    fetcher = ClearlyDefinedLicenseFetcher(http=make_mock_client(handler))
    fetcher.fetch("pkg:npm/@babel/core@7.24.0")
    assert seen == ["/definitions/npm/npmjs/%40babel/core/7.24.0"]
    # Five segments after /definitions, not six.
    assert seen[0].count("/") == 6
