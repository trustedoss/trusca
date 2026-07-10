"""Unit tests — endoflife.date feed client (Phase M, PR M-3).

Same harness as ``test_kev_feed.py``: an ``httpx.MockTransport``-backed
client injected through the ``http=`` parameter, so no network ever runs.
Pinned contracts:

  * per-product failures are skipped and counted — one broken product never
    discards the others (the KEV per-entry posture at product granularity);
  * :class:`EolFeedUnavailable` fires only when NOTHING fetched;
  * the per-product byte ceiling and the non-list document guard;
  * the assembled dataset carries ``_snapshot`` and only the evaluator's
    field set (compact — the persisted JSONB stays a few KB).
"""

from __future__ import annotations

import json

import httpx
import pytest

from integrations.eol_feed import (
    EolFeedUnavailable,
    fetch_eol_dataset,
)

_GOOD_CYCLES = [
    {
        "cycle": "4",
        "eol": False,
        "latest": "4.21.0",
        "releaseDate": "2014-04-09",
        "link": "https://example.com/ignored",  # NOT in _KEEP_FIELDS
        "lts": True,  # NOT in _KEEP_FIELDS
    },
    {"cycle": "3", "eol": True},
]


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_happy_path_compacts_fields_and_stamps_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_GOOD_CYCLES)

    result = fetch_eol_dataset(["express", "django"], http=_client(handler))
    assert result.fetched == ["express", "django"]
    assert result.failed == []
    assert isinstance(result.dataset["_snapshot"], str)
    express = result.dataset["express"]
    assert express[0] == {
        "cycle": "4",
        "eol": False,
        "latest": "4.21.0",
        "releaseDate": "2014-04-09",
    }  # link/lts stripped — compact persisted shape


def test_partial_failure_skips_and_counts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "django" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, json=_GOOD_CYCLES)

    result = fetch_eol_dataset(["express", "django"], http=_client(handler))
    assert result.fetched == ["express"]
    assert result.failed == ["django"]
    assert "django" not in result.dataset


def test_all_failed_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(EolFeedUnavailable):
        fetch_eol_dataset(["express", "django"], http=_client(handler))


def test_non_list_document_counts_as_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "django" in str(request.url):
            return httpx.Response(200, json={"not": "a list"})
        return httpx.Response(200, json=_GOOD_CYCLES)

    result = fetch_eol_dataset(["express", "django"], http=_client(handler))
    assert result.failed == ["django"]


def test_invalid_json_counts_as_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "django" in str(request.url):
            return httpx.Response(200, content=b"{ not json")
        return httpx.Response(200, json=_GOOD_CYCLES)

    result = fetch_eol_dataset(["express", "django"], http=_client(handler))
    assert result.failed == ["django"]


def test_oversized_product_document_counts_as_failed() -> None:
    huge = json.dumps([{"cycle": str(i), "eol": False} for i in range(200_000)])
    assert len(huge) > 2 * 1024 * 1024  # over the per-product ceiling

    def handler(request: httpx.Request) -> httpx.Response:
        if "django" in str(request.url):
            return httpx.Response(200, content=huge.encode())
        return httpx.Response(200, json=_GOOD_CYCLES)

    result = fetch_eol_dataset(["express", "django"], http=_client(handler))
    assert result.failed == ["django"]
    assert result.fetched == ["express"]


def test_network_error_counts_as_failed_without_url_leak() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "django" in str(request.url):
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=_GOOD_CYCLES)

    result = fetch_eol_dataset(["express", "django"], http=_client(handler))
    assert result.failed == ["django"]


def test_template_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "EOL_FEED_URL_TEMPLATE", "https://mirror.internal/eol/{product}.json"
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_GOOD_CYCLES)

    fetch_eol_dataset(["express"], http=_client(handler))
    assert seen == ["https://mirror.internal/eol/express.json"]
