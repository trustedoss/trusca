"""
Unit tests for the CISA KEV feed client (``integrations/kev_feed.py``).

Fixture policy (hardening rule 3): the parser is driven by a REAL captured
CISA feed excerpt (``tests/fixtures/kev/cisa-kev-excerpt.json`` — 12 entries
including log4shell CVE-2021-44228), never a hand-built minimal JSON.
Adversarial-input cases layer targeted mutations ON TOP of the real shape.

Also asserts the ``core.config`` KEV accessor defaults / overrides (same
snapshot-free monkeypatch style as ``test_concurrency_config.py`` — every
accessor reads os.getenv at call time per CLAUDE.md core rule #11).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.config import (
    kev_feed_url,
    kev_refresh_enabled,
    kev_refresh_timeout_seconds,
)
from integrations import kev_feed
from integrations.kev_feed import (
    KevEntry,
    KevFeedUnavailable,
    fetch_kev_catalog,
    parse_kev_catalog,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "kev" / "cisa-kev-excerpt.json"
)

_DEFAULT_FEED_URL = (
    "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
)


@pytest.fixture
def fixture_payload() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(FIXTURE_PATH.read_text())
    return payload


# ---------------------------------------------------------------------------
# parse_kev_catalog — real captured fixture
# ---------------------------------------------------------------------------


def test_parse_fixture_yields_all_twelve_entries(fixture_payload: dict[str, Any]) -> None:
    catalog = parse_kev_catalog(fixture_payload)
    assert len(catalog) == 12
    assert all(isinstance(entry, KevEntry) for entry in catalog.values())


def test_parse_fixture_log4shell_dates(fixture_payload: dict[str, Any]) -> None:
    """The log4shell entry carries the exact CISA listing / due dates."""
    catalog = parse_kev_catalog(fixture_payload)
    entry = catalog["CVE-2021-44228"]
    assert entry.date_added == date(2021, 12, 10)
    assert entry.due_date == date(2021, 12, 24)


def test_parse_keys_are_uppercased(fixture_payload: dict[str, Any]) -> None:
    """A lowercase cveID in the feed still lands under the upper-case key."""
    fixture_payload["vulnerabilities"].append(
        {"cveID": "cve-2099-0001", "dateAdded": "2026-01-01", "dueDate": "2026-01-22"}
    )
    catalog = parse_kev_catalog(fixture_payload)
    assert "CVE-2099-0001" in catalog
    assert catalog["CVE-2099-0001"].date_added == date(2026, 1, 1)


# ---------------------------------------------------------------------------
# parse_kev_catalog — adversarial / malformed input defence
# ---------------------------------------------------------------------------


def test_parse_empty_dict_raises_unavailable() -> None:
    with pytest.raises(KevFeedUnavailable):
        parse_kev_catalog({})


def test_parse_non_dict_payload_raises_unavailable() -> None:
    junk: Any
    for junk in ([], "string", 42, None):
        with pytest.raises(KevFeedUnavailable):
            parse_kev_catalog(junk)


def test_parse_vulnerabilities_not_array_raises_unavailable() -> None:
    for junk in ({"vulnerabilities": "not-a-list"}, {"vulnerabilities": {"a": 1}}):
        with pytest.raises(KevFeedUnavailable):
            parse_kev_catalog(junk)


def test_parse_skips_malformed_entries_keeps_the_rest(
    fixture_payload: dict[str, Any],
) -> None:
    """Per-entry defects are skipped item-by-item, never raised — one bad row
    must not discard the other 12 real ones."""
    fixture_payload["vulnerabilities"].extend(
        [
            "not-a-dict",
            42,
            None,
            {},  # missing cveID
            {"cveID": ""},  # blank cveID
            {"cveID": "   "},  # whitespace-only cveID
            {"cveID": 12345},  # non-string cveID
            {"cveID": "C" * 65},  # over the String(64) column cap
        ]
    )
    catalog = parse_kev_catalog(fixture_payload)
    assert len(catalog) == 12  # only the real fixture entries survive


def test_parse_unparseable_dates_become_none_entry_survives(
    fixture_payload: dict[str, Any],
) -> None:
    """A listed CVE with garbage date fields is still KEV — dates land as None."""
    fixture_payload["vulnerabilities"].append(
        {"cveID": "CVE-2099-0002", "dateAdded": "not-a-date", "dueDate": 99}
    )
    catalog = parse_kev_catalog(fixture_payload)
    entry = catalog["CVE-2099-0002"]
    assert entry.date_added is None
    assert entry.due_date is None


def test_parse_missing_date_fields_become_none(fixture_payload: dict[str, Any]) -> None:
    fixture_payload["vulnerabilities"].append({"cveID": "CVE-2099-0003"})
    entry = parse_kev_catalog(fixture_payload)["CVE-2099-0003"]
    assert entry.date_added is None
    assert entry.due_date is None


def test_parse_entry_count_over_ceiling_raises_unavailable(
    fixture_payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """MINOR-2 — a document over the entry ceiling is refused whole (never
    truncated: entries past a truncation point would read as 'removed' and
    feed the delist pass). Ceiling lowered so the 12-entry REAL fixture
    exercises the branch without building a 50k-entry payload."""
    monkeypatch.setattr(kev_feed, "_MAX_FEED_ENTRIES", 5)
    with pytest.raises(KevFeedUnavailable):
        parse_kev_catalog(fixture_payload)


# ---------------------------------------------------------------------------
# fetch_kev_catalog — HTTP layer (MockTransport-backed injected client)
# ---------------------------------------------------------------------------


def _client_returning(response: httpx.Response) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return response

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_parses_fixture_bytes() -> None:
    with _client_returning(
        httpx.Response(200, content=FIXTURE_PATH.read_bytes())
    ) as client:
        catalog = fetch_kev_catalog(http=client)
    assert len(catalog) == 12
    assert catalog["CVE-2021-44228"].due_date == date(2021, 12, 24)


def test_fetch_http_error_raises_unavailable() -> None:
    for status in (404, 500, 503):
        with _client_returning(httpx.Response(status, content=b"nope")) as client:
            with pytest.raises(KevFeedUnavailable):
                fetch_kev_catalog(http=client)


def test_fetch_network_failure_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(KevFeedUnavailable):
            fetch_kev_catalog(http=client)


def test_fetch_invalid_json_raises_unavailable() -> None:
    with _client_returning(httpx.Response(200, content=b"{ not json !!")) as client:
        with pytest.raises(KevFeedUnavailable):
            fetch_kev_catalog(http=client)


def test_fetch_oversized_body_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body over the byte ceiling is refused mid-stream (never fully buffered)."""
    monkeypatch.setattr(kev_feed, "_MAX_FEED_BYTES", 64)
    with _client_returning(httpx.Response(200, content=b"x" * 1024)) as client:
        with pytest.raises(KevFeedUnavailable):
            fetch_kev_catalog(http=client)


def test_fetch_wall_clock_deadline_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MINOR-1 — the whole-transfer deadline fires even when every individual
    chunk arrives inside the per-operation timeout (slow-drip defence). An
    already-expired deadline makes the very first chunk trip it."""
    monkeypatch.setattr(kev_feed, "_FETCH_DEADLINE_SECONDS", -1)
    with _client_returning(
        httpx.Response(200, content=FIXTURE_PATH.read_bytes())
    ) as client:
        with pytest.raises(KevFeedUnavailable):
            fetch_kev_catalog(http=client)


def test_fetch_schemeless_url_raises_unavailable_without_url_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFO — a schemeless KEV_FEED_URL typo surfaces from httpx 0.28's send
    path as a BARE ValueError (not an HTTPError); it must land in the same
    catch and surface only the exception TYPE — never the URL, which may
    carry a mirror auth token."""
    monkeypatch.setenv("KEV_FEED_URL", "://user:token@not-a-valid-url")
    with _client_returning(httpx.Response(200, content=b"{}")) as client:
        with pytest.raises(KevFeedUnavailable) as excinfo:
            fetch_kev_catalog(http=client)
    assert "token" not in str(excinfo.value)


def test_fetch_invalid_url_exception_raises_unavailable_without_url_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFO — ``httpx.InvalidURL`` (raised for e.g. non-printable characters
    in the URL) is NOT an HTTPError subclass; same catch, type-name-only
    message. The URL is injected via the accessor because os.environ cannot
    hold a NUL byte."""
    monkeypatch.setattr(
        kev_feed, "kev_feed_url", lambda: "http://user:token@\x00evil/kev.json"
    )
    with _client_returning(httpx.Response(200, content=b"{}")) as client:
        with pytest.raises(KevFeedUnavailable) as excinfo:
            fetch_kev_catalog(http=client)
    assert "token" not in str(excinfo.value)


def test_fetch_plain_http_scheme_still_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFO — an http:// mirror is allowed (operator choice, warned host-only),
    not blocked; the fetch itself proceeds normally."""
    monkeypatch.setenv("KEV_FEED_URL", "http://mirror.internal/kev.json")
    with _client_returning(
        httpx.Response(200, content=FIXTURE_PATH.read_bytes())
    ) as client:
        catalog = fetch_kev_catalog(http=client)
    assert len(catalog) == 12


# ---------------------------------------------------------------------------
# core.config accessors — defaults + runtime overrides (rule #11)
# ---------------------------------------------------------------------------


def test_kev_feed_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEV_FEED_URL", raising=False)
    assert kev_feed_url() == _DEFAULT_FEED_URL


def test_kev_feed_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KEV_FEED_URL", "https://mirror.internal/kev.json")
    assert kev_feed_url() == "https://mirror.internal/kev.json"


def test_kev_refresh_enabled_default_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KEV_REFRESH_ENABLED", raising=False)
    assert kev_refresh_enabled() is True


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_kev_refresh_enabled_truthy_tokens(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("KEV_REFRESH_ENABLED", raw)
    assert kev_refresh_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "junk", ""])
def test_kev_refresh_enabled_falsy_tokens(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("KEV_REFRESH_ENABLED", raw)
    assert kev_refresh_enabled() is False


def test_kev_refresh_timeout_default_and_clamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KEV_REFRESH_TIMEOUT_SECONDS", raising=False)
    assert kev_refresh_timeout_seconds() == 30

    monkeypatch.setenv("KEV_REFRESH_TIMEOUT_SECONDS", "120")
    assert kev_refresh_timeout_seconds() == 120

    # Junk falls back to the default; out-of-range clamps to [1, 600].
    monkeypatch.setenv("KEV_REFRESH_TIMEOUT_SECONDS", "not-a-number")
    assert kev_refresh_timeout_seconds() == 30
    monkeypatch.setenv("KEV_REFRESH_TIMEOUT_SECONDS", "0")
    assert kev_refresh_timeout_seconds() == 1
    monkeypatch.setenv("KEV_REFRESH_TIMEOUT_SECONDS", "99999")
    assert kev_refresh_timeout_seconds() == 600
