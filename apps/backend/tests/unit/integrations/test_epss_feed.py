"""
Unit tests for the EPSS feed client (``integrations/epss_feed.py``).

Fixture policy (hardening rule 3): the parser is driven by a REAL captured
excerpt of the published daily CSV
(``tests/fixtures/epss/epss-scores-excerpt.csv.gz``: the leading
``#model_version`` comment, the real header, and twelve real rows including
CVE-2021-44228 at 0.99999), never a hand-built minimal document.

That policy is the reason this feature exists at all. ``_extract_epss`` in the
matching layer had unit tests whose inputs were hand-written dicts carrying an
``EPSS`` key, so the parser looked correct while the scanner it parses never
emits that key on any path. A fixture written from what the code expects
cannot discover that the source does not send it. Adversarial cases below
layer targeted mutations ON TOP of the real document rather than replacing it.

Also asserts the ``core.config`` EPSS accessor defaults and overrides (same
monkeypatch style as the KEV tests; every accessor reads ``os.getenv`` at call
time per CLAUDE.md core rule #11).
"""

from __future__ import annotations

import gzip
import io
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from core.config import (
    epss_feed_url,
    epss_refresh_enabled,
    epss_refresh_timeout_seconds,
)
from integrations.epss_feed import (
    EpssFeedUnavailable,
    fetch_epss_scores,
    parse_epss_csv,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "epss"
    / "epss-scores-excerpt.csv.gz"
)

_DEFAULT_FEED_URL = "https://epss.empiricalsecurity.com/epss_scores-current.csv.gz"


def _fixture_bytes() -> bytes:
    return FIXTURE_PATH.read_bytes()


def _fixture_text() -> str:
    return gzip.decompress(_fixture_bytes()).decode("utf-8")


def _stream(text: str) -> io.StringIO:
    return io.StringIO(text)


# ---------------------------------------------------------------------------
# parse_epss_csv: the real document
# ---------------------------------------------------------------------------


def test_parses_the_real_excerpt_with_its_real_values() -> None:
    """The published values land as the column stores them.

    Not "a score is present" but the actual numbers: an assertion that only
    checks for non-NULL passes against a parser that puts the percentile in
    the score column, which is exactly the confusion the header check guards.
    """
    feed = parse_epss_csv(_stream(_fixture_text()))

    assert feed.rows_read == 12
    assert len(feed.scores) == 12
    log4shell = feed.scores["CVE-2021-44228"]
    assert log4shell.score == Decimal("0.99999")
    assert log4shell.percentile == Decimal("1.00000")
    # A mid-range row, so a parser that clamped or rounded everything to the
    # extremes would not pass on log4shell alone.
    assert feed.scores["CVE-2015-3153"].score == Decimal("0.07247")
    assert feed.scores["CVE-2015-3153"].percentile == Decimal("0.93898")


def test_reads_the_model_metadata_off_the_comment_line() -> None:
    """Which model run produced these scores is a question the values cannot answer."""
    feed = parse_epss_csv(_stream(_fixture_text()))

    assert feed.model_version == "v2026.06.15"
    assert feed.score_date is not None
    assert feed.score_date.year == 2026
    assert feed.score_date.tzinfo is not None


def test_keeps_only_the_cves_the_caller_asked_for() -> None:
    """Rows outside the deployment's catalog are dropped as they stream past.

    This is what keeps peak memory a function of the catalog rather than of a
    367,000-row feed.
    """
    feed = parse_epss_csv(
        _stream(_fixture_text()), wanted={"CVE-2021-44228", "CVE-2016-0800"}
    )

    assert set(feed.scores) == {"CVE-2021-44228", "CVE-2016-0800"}
    # Every row was still READ; only the matches were kept.
    assert feed.rows_read == 12


# ---------------------------------------------------------------------------
# parse_epss_csv: adversarial, layered on the real document
# ---------------------------------------------------------------------------


def test_a_changed_header_is_refused_rather_than_read_positionally() -> None:
    """Swapping the columns must fail loudly, not silently invert the data.

    Reading positionally past a renamed header would put percentiles into
    ``epss_score``, and every value would still look like a plausible
    probability.
    """
    text = _fixture_text().replace(
        "cve,epss,percentile", "cve,percentile,epss", 1
    )
    with pytest.raises(EpssFeedUnavailable, match="header"):
        parse_epss_csv(_stream(text))


def test_an_empty_document_is_refused() -> None:
    with pytest.raises(EpssFeedUnavailable, match="empty"):
        parse_epss_csv(_stream(""))


def test_a_document_without_the_comment_line_still_parses() -> None:
    """The metadata is for the panel; its absence is not a reason to refuse rows."""
    lines = _fixture_text().split("\n")
    assert lines[0].startswith("#")
    feed = parse_epss_csv(_stream("\n".join(lines[1:])))

    assert len(feed.scores) == 12
    assert feed.model_version is None
    assert feed.score_date is None


@pytest.mark.parametrize(
    "bad_row",
    [
        "CVE-2020-0001,notanumber,0.5",  # unparseable score
        "CVE-2020-0001,0.5,notanumber",  # unparseable percentile
        "CVE-2020-0001,1.5,0.5",  # score outside [0, 1]
        "CVE-2020-0001,-0.1,0.5",  # negative score
        "CVE-2020-0001,NaN,0.5",  # parses as Decimal, is not a probability
        "CVE-2020-0001,Infinity,0.5",
        "CVE-2020-0001,0.5",  # too few columns
        "CVE-2020-0001,0.5,0.5,extra",  # too many columns
        ",0.5,0.5",  # blank id
        "C" * 65 + ",0.5,0.5",  # id longer than external_id can hold
    ],
)
def test_one_bad_row_is_skipped_and_the_rest_survive(bad_row: str) -> None:
    """A handful of bad rows in 367,000 must not discard the good ones."""
    text = _fixture_text().rstrip("\n") + "\n" + bad_row + "\n"
    feed = parse_epss_csv(_stream(text))

    assert len(feed.scores) == 12
    assert feed.scores["CVE-2021-44228"].score == Decimal("0.99999")


def test_the_last_row_wins_for_a_duplicated_cve() -> None:
    """A duplicate is not a defect worth discarding the document over."""
    text = _fixture_text().rstrip("\n") + "\nCVE-2021-44228,0.11111,0.22222\n"
    feed = parse_epss_csv(_stream(text))

    assert feed.scores["CVE-2021-44228"].score == Decimal("0.11111")


def test_a_lower_case_cve_id_matches_the_catalog() -> None:
    """Ids are normalised on both sides rather than trusting them to agree."""
    text = _fixture_text().replace("CVE-2021-44228", "cve-2021-44228", 1)
    feed = parse_epss_csv(_stream(text), wanted={"CVE-2021-44228"})

    assert "CVE-2021-44228" in feed.scores


# ---------------------------------------------------------------------------
# fetch_epss_scores: transport
# ---------------------------------------------------------------------------


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_fetch_decompresses_and_returns_the_matched_scores() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_fixture_bytes())

    with _client(handler) as http:
        feed = fetch_epss_scores(wanted={"CVE-2021-44228"}, http=http)

    assert feed.scores["CVE-2021-44228"].score == Decimal("0.99999")


def test_a_non_200_is_a_skip_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"upstream is having a day")

    with _client(handler) as http:
        with pytest.raises(EpssFeedUnavailable, match="503"):
            fetch_epss_scores(wanted=set(), http=http)


def test_a_body_that_is_not_gzip_is_a_document_level_failure() -> None:
    """Served plain by a misconfigured mirror, or garbage from a hostile one."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"cve,epss,percentile\nCVE-1,0.5,0.5\n")

    with _client(handler) as http:
        with pytest.raises(EpssFeedUnavailable, match="gzip"):
            fetch_epss_scores(wanted=set(), http=http)


def test_a_gzip_bomb_is_refused_by_the_decompressed_ceiling() -> None:
    """The compressed cap does not bound the expansion ratio, so this one does.

    A few hundred KiB of zeros expands past the decompressed ceiling; without
    that ceiling the worker would buffer it all.
    """
    bomb = gzip.compress(b"\0" * (300 * 1024 * 1024))
    assert len(bomb) < 1024 * 1024, "the bomb must be small compressed to be a bomb"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=bomb)

    with _client(handler) as http:
        with pytest.raises(EpssFeedUnavailable, match="decompressed"):
            fetch_epss_scores(wanted=set(), http=http)


def test_a_network_failure_is_a_skip_and_does_not_leak_the_url() -> None:
    """A mirror URL can carry an auth token, so the message names the type only."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with _client(handler) as http:
        with pytest.raises(EpssFeedUnavailable) as exc:
            fetch_epss_scores(wanted=set(), http=http)

    assert "epss.empiricalsecurity.com" not in str(exc.value)
    assert "ConnectError" in str(exc.value)


# ---------------------------------------------------------------------------
# core.config accessors
# ---------------------------------------------------------------------------


def test_feed_url_default_is_the_bulk_csv_not_the_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FIRST's guidance is that the API must not be used for bulk sync."""
    monkeypatch.delenv("EPSS_FEED_URL", raising=False)

    assert epss_feed_url() == _DEFAULT_FEED_URL
    assert "api.first.org" not in epss_feed_url()


def test_feed_url_is_overridable_for_an_air_gapped_mirror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EPSS_FEED_URL", "https://mirror.internal/epss.csv.gz")

    assert epss_feed_url() == "https://mirror.internal/epss.csv.gz"


def test_refresh_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Installing the product must not reach the public internet by itself."""
    monkeypatch.delenv("EPSS_REFRESH_ENABLED", raising=False)

    assert epss_refresh_enabled() is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "Yes", "on"])
def test_refresh_accepts_the_usual_truthy_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("EPSS_REFRESH_ENABLED", raw)

    assert epss_refresh_enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "maybe"])
def test_refresh_treats_anything_else_as_off(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("EPSS_REFRESH_ENABLED", raw)

    assert epss_refresh_enabled() is False


def test_timeout_default_and_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPSS_REFRESH_TIMEOUT_SECONDS", raising=False)
    assert epss_refresh_timeout_seconds() == 60

    monkeypatch.setenv("EPSS_REFRESH_TIMEOUT_SECONDS", "5")
    assert epss_refresh_timeout_seconds() == 5

    monkeypatch.setenv("EPSS_REFRESH_TIMEOUT_SECONDS", "99999")
    assert epss_refresh_timeout_seconds() == 600

    monkeypatch.setenv("EPSS_REFRESH_TIMEOUT_SECONDS", "not-a-number")
    assert epss_refresh_timeout_seconds() == 60
