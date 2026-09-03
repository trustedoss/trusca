# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
EPSS (Exploit Prediction Scoring System) daily score feed client.

Downloads the daily gzipped CSV of EPSS scores and returns the rows matching
CVEs this deployment already knows about, for
``tasks.epss_catalog_refresh`` to write onto ``vulnerabilities.epss_score``
and ``vulnerabilities.epss_percentile``.

Source and attribution
----------------------
Feed: ``https://epss.empiricalsecurity.com/epss_scores-current.csv.gz``
(daily, unauthenticated; ``-current`` redirects to that day's dated file).
EPSS is stewarded by the EPSS Special Interest Group at FIRST
(https://www.first.org/epss); the scores are generated and published by
Empirical Security. FIRST asks that attribution be given where possible when
EPSS data is used in a product; TRUSCA carries it in
``THIRD_PARTY_NOTICES.md``, in the data-sources reference page, and here. The
citation FIRST supplies is:

    Jay Jacobs, Sasha Romanosky, Benjamin Edwards, Michael Roytman,
    Idris Adjerid (2021), Exploit Prediction Scoring System,
    Digital Threats Research and Practice, 2(3)

TRUSCA claims no ownership of this data, and neither FIRST nor Empirical
Security endorses TRUSCA. Nothing is redistributed: each installation fetches
the CSV itself into its own database, and the file is not vendored into the
source tree, the images, or any release artefact.

Why the bulk CSV and not the API
--------------------------------
FIRST's own guidance on https://www.first.org/epss/data is that the lookup API
"is designed for lookup, not bulk access" and "should not be used for bulk
downloads or to keep a local copy of all scores in sync; the daily CSV or the
GitHub repository is the right mechanism for that". Using the API the way this
task needs would violate that guidance, and it would also pull the traffic
under FIRST's Services Terms of Use, whose licence grant is revocable and
non-transferable. The CSV is served by Empirical Security rather than by
FIRST's own services, so the bulk path is both the sanctioned one and the one
with fewer strings. Do not switch this module to ``api.first.org``.

Feed shape (stable since 2021)::

    #model_version:v2026.06.15,score_date:2026-09-02T12:00:22Z
    cve,epss,percentile
    CVE-1999-0001,0.03351,0.87851
    ...

The first line is a comment carrying the model version and the scoring
timestamp; the second is the CSV header. Measured 2026-09-02: 2.6 MB gzipped,
367,327 rows.

Why the caller passes the CVEs it wants
---------------------------------------
The whole feed is roughly 220 times the size of the KEV catalog, and a
deployment's ``vulnerabilities`` table holds the few hundred to few thousand
CVEs its own scans have actually seen. Materialising all 367k rows to then
discard 99% of them would make peak memory a function of the feed rather than
of the deployment, so the caller hands in the set it can use and rows outside
it are dropped as they stream past. Memory is bounded by the catalog, not by
the feed.

Trust model / URL guard:
    The feed URL comes exclusively from operator env configuration
    (``EPSS_FEED_URL``, read at call time per CLAUDE.md core rule #11), so
    like the KEV feed it deliberately does NOT route through
    ``core.url_guard``: there is no user-supplied write path to it, and an
    operator who can set worker env vars already controls the process. The
    same env var is how an air-gapped deployment points at an internal
    mirror.

Adversarial-input posture:
    The feed is third-party data crossing a trust boundary, so parsing is
    defensive end to end. A response over the byte ceiling, a body that is
    not valid gzip, a decompressed stream over its own ceiling, a transfer
    past the wall-clock deadline, or a document with no usable header raise
    :class:`EpssFeedUnavailable` (the refresh task catches it and skips the
    tick, never crashing the beat). Per-row defects (wrong column count,
    unparseable numbers, out-of-range probabilities, over-long ids) are
    SKIPPED row by row with one summary WARNING, never raised: a handful of
    bad rows in 367k must not discard the rest.
"""

from __future__ import annotations

import csv
import gzip
import io
import time
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from core.config import epss_feed_url, epss_refresh_timeout_seconds

log = structlog.get_logger("integrations.epss_feed")

# Hard ceiling on the COMPRESSED body. The real file is ~2.6 MiB as of
# 2026-09; 32 MiB leaves an order of magnitude of growth headroom while
# capping what a misconfigured or hostile mirror can make the worker buffer.
_MAX_COMPRESSED_BYTES = 32 * 1024 * 1024

# Ceiling on the DECOMPRESSED stream, checked as it is produced. Without it a
# gzip bomb inside a 2 MiB body could expand without bound: the compressed cap
# alone does not constrain the expansion ratio. The real file decompresses to
# ~8 MiB, so 256 MiB is far past any legitimate feed and still a size the
# worker can refuse before it hurts.
_MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024

# Whole-transfer wall-clock deadline. ``EPSS_REFRESH_TIMEOUT_SECONDS`` is
# httpx's PER-OPERATION timeout, so a slow-drip server can hold a worker far
# past the operator's intent without ever tripping it. Same reasoning and same
# value as the KEV client.
_FETCH_DEADLINE_SECONDS = 300

# Ceiling on rows read from one document. The real feed is ~367k rows and
# grows with the CVE corpus, so this is deliberately far above it: unlike the
# KEV cap this is NOT a bind-parameter limit (the caller filters to its own
# catalog before any SQL sees these), it is only a runaway guard.
_MAX_FEED_ROWS = 5_000_000

# ``vulnerabilities.external_id`` is String(64) and no real CVE id approaches
# it. Longer values are junk and are skipped rather than truncated, because a
# truncated id would silently match nothing.
_MAX_CVE_ID_LEN = 64

# The columns EPSS has published since 2021. Checked rather than assumed so a
# reordered or renamed header is a clean document-level failure instead of
# scores silently landing in the percentile column.
_EXPECTED_HEADER = ("cve", "epss", "percentile")

# ``vulnerabilities.epss_score`` / ``epss_percentile`` are NUMERIC(6,5), so a
# value is stored to five decimal places. Quantising here rather than letting
# the database round means the comparison the refresh task makes ("did this
# score change") is against the value that will actually be stored.
_QUANTUM = Decimal("0.00001")


class EpssFeedUnavailable(Exception):
    """The EPSS feed could not be fetched or parsed at the document level.

    Raised for network failures, non-2xx responses, oversized bodies (before
    or after decompression), corrupt gzip, a transfer past the deadline, and a
    document whose header is not the published one. Callers treat this as
    "skip this tick and retry on the next beat"; it is never a data-corruption
    signal.
    """


@dataclass(frozen=True)
class EpssEntry:
    """One CVE's EPSS scores, already quantised to what the column stores.

    ``score``: probability of exploitation in the next 30 days, in [0, 1].
    ``percentile``: this CVE's rank among all scored CVEs, in [0, 1].
    """

    score: Decimal
    percentile: Decimal


@dataclass(frozen=True)
class EpssFeed:
    """A parsed feed: the matched scores plus what the document said about itself.

    ``model_version`` and ``score_date`` come from the leading comment line and
    are recorded on the sync-state row, so an operator can see which model run
    produced the scores currently in the catalog. Either is ``None`` when the
    comment was absent or unparseable; that is not a reason to refuse a
    document whose rows are fine.
    """

    scores: dict[str, EpssEntry]
    model_version: str | None
    score_date: datetime | None
    rows_read: int


def _safe_host(url: str) -> str:
    """Return only the host component for logging, never the full URL."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid>"
    return parsed.hostname or "<no-host>"


def _parse_probability(raw: str) -> Decimal | None:
    """Parse an EPSS probability to the precision the column stores.

    Returns ``None`` for anything that is not a real number in [0, 1]:
    NaN and the infinities parse as ``Decimal`` but are not probabilities, and
    a value outside the range means the row is wrong about something more
    fundamental than precision.
    """
    try:
        value = Decimal(raw.strip())
    except Exception:  # noqa: BLE001 - any malformed number is just a skip
        return None
    if not value.is_finite():
        return None
    if value < 0 or value > 1:
        return None
    return value.quantize(_QUANTUM)


def _parse_metadata(comment: str) -> tuple[str | None, datetime | None]:
    """Pull ``model_version`` and ``score_date`` out of the leading comment.

    Shape: ``#model_version:v2026.06.15,score_date:2026-09-02T12:00:22Z``.
    Anything unrecognised yields ``(None, None)`` rather than an error, since
    the scores are what the task needs and the metadata is for the panel.
    """
    if not comment.startswith("#"):
        return None, None
    model_version: str | None = None
    score_date: datetime | None = None
    for field in comment[1:].split(","):
        key, _, value = field.partition(":")
        key = key.strip()
        value = value.strip()
        if not value:
            continue
        if key == "model_version":
            model_version = value[:64]
        elif key == "score_date":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
            score_date = (
                parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            )
    return model_version, score_date


def parse_epss_csv(text_stream: Any, wanted: set[str] | None = None) -> EpssFeed:
    """Parse the decompressed CSV, keeping only the CVEs in ``wanted``.

    ``wanted`` holds upper-cased CVE ids; ``None`` keeps every row and is for
    tests and one-off inspection, not for the refresh task (see the module
    docstring on why the caller filters).

    Row-level defects are skipped and summarised in ONE warning. A missing or
    unexpected header is a document-level defect and raises, because reading
    positionally past a changed header would put percentiles into the score
    column without anything noticing.
    """
    first = text_stream.readline()
    if not first:
        raise EpssFeedUnavailable("EPSS feed is empty")
    model_version, score_date = _parse_metadata(first.strip())
    header_line = first if not first.startswith("#") else text_stream.readline()
    header = tuple(part.strip().lower() for part in header_line.strip().split(","))
    if header != _EXPECTED_HEADER:
        raise EpssFeedUnavailable(
            f"EPSS feed header is {header!r}, expected {_EXPECTED_HEADER!r}"
        )

    scores: dict[str, EpssEntry] = {}
    skipped = 0
    rows_read = 0
    for row in csv.reader(text_stream):
        rows_read += 1
        if rows_read > _MAX_FEED_ROWS:
            raise EpssFeedUnavailable(
                f"EPSS feed carries more than {_MAX_FEED_ROWS} rows"
            )
        if len(row) != 3:
            skipped += 1
            continue
        cve = row[0].strip().upper()
        if not cve or len(cve) > _MAX_CVE_ID_LEN:
            skipped += 1
            continue
        if wanted is not None and cve not in wanted:
            continue
        score = _parse_probability(row[1])
        percentile = _parse_probability(row[2])
        if score is None or percentile is None:
            skipped += 1
            continue
        scores[cve] = EpssEntry(score=score, percentile=percentile)

    if skipped:
        # One summary warning for the whole pass; per-row logging on a hostile
        # feed would be its own log-flood vector.
        log.warning("epss_feed_rows_skipped", skipped=skipped, parsed=len(scores))

    return EpssFeed(
        scores=scores,
        model_version=model_version,
        score_date=score_date,
        rows_read=rows_read,
    )


def fetch_epss_scores(
    *, wanted: set[str], http: httpx.Client | None = None
) -> EpssFeed:
    """Download the daily CSV and return the scores for ``wanted``.

    Args:
        wanted: Upper-cased CVE ids this deployment can use. Rows outside it
            are discarded as they stream past, so peak memory tracks the
            deployment's catalog rather than the feed.
        http: Optional pre-built client (tests inject an
            ``httpx.MockTransport``-backed one). When ``None`` a short-lived
            client is created with ``EPSS_REFRESH_TIMEOUT_SECONDS`` and closed
            before returning. ``follow_redirects=True`` is required, not
            merely safe: ``-current`` is published as a redirect to the dated
            file of the day.

    Raises:
        EpssFeedUnavailable: any document-level failure (see the class).
    """
    url = epss_feed_url()
    safe_host = _safe_host(url)
    if url.strip().lower().startswith("http://"):
        # An operator may legitimately point at a plaintext internal mirror,
        # so this is a warning rather than a refusal, but a typo'd scheme on
        # the public internet should not pass unremarked. Host only.
        log.warning("epss_feed_insecure_scheme", host=safe_host)

    owned = http is None
    client = http or httpx.Client(
        timeout=epss_refresh_timeout_seconds(),
        follow_redirects=True,
        headers={"User-Agent": "TRUSCA/epss-sync (+https://github.com/trustedoss/trusca)"},
    )
    started = time.monotonic()
    try:
        try:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    log.warning(
                        "epss_feed_http_error",
                        host=safe_host,
                        status=response.status_code,
                    )
                    raise EpssFeedUnavailable(
                        f"EPSS feed returned HTTP {response.status_code}"
                    )
                compressed = _read_capped(response, safe_host=safe_host, started=started)
        except (
            httpx.TimeoutException,
            httpx.HTTPError,
            # Neither of these is an HTTPError subclass on current httpx, and
            # their str() embeds the full URL, which may carry a mirror auth
            # token. Same reasoning as the KEV client.
            httpx.InvalidURL,
            httpx.UnsupportedProtocol,
        ) as exc:
            log.warning("epss_feed_fetch_failed", host=safe_host, error=type(exc).__name__)
            raise EpssFeedUnavailable(
                f"EPSS feed fetch failed: {type(exc).__name__}"
            ) from exc
    finally:
        if owned:
            client.close()

    text_stream = _decompress(compressed, safe_host=safe_host)
    feed = parse_epss_csv(text_stream, wanted=wanted)
    log.info(
        "epss_feed_parsed",
        host=safe_host,
        rows_read=feed.rows_read,
        matched=len(feed.scores),
        model_version=feed.model_version,
    )
    return feed


def _read_capped(response: httpx.Response, *, safe_host: str, started: float) -> bytes:
    """Buffer the response body under both the byte cap and the deadline."""
    buf = bytearray()
    for chunk in response.iter_bytes():
        if time.monotonic() - started > _FETCH_DEADLINE_SECONDS:
            log.warning(
                "epss_feed_deadline_exceeded",
                host=safe_host,
                deadline_seconds=_FETCH_DEADLINE_SECONDS,
            )
            raise EpssFeedUnavailable(
                f"EPSS feed transfer exceeded {_FETCH_DEADLINE_SECONDS}s deadline"
            )
        buf.extend(chunk)
        if len(buf) > _MAX_COMPRESSED_BYTES:
            log.warning(
                "epss_feed_too_large", host=safe_host, limit_bytes=_MAX_COMPRESSED_BYTES
            )
            raise EpssFeedUnavailable(
                f"EPSS feed exceeded {_MAX_COMPRESSED_BYTES} compressed bytes"
            )
    return bytes(buf)


def _decompress(compressed: bytes, *, safe_host: str) -> io.StringIO:
    """Gunzip under a decompressed-size ceiling.

    The compressed cap does not bound the expansion ratio, so this reads in
    bounded steps and refuses a body that keeps producing output past
    ``_MAX_DECOMPRESSED_BYTES``. A body that is not gzip at all is a
    document-level failure, not a row-level one.
    """
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as gz:
            out = bytearray()
            while True:
                chunk = gz.read(1024 * 1024)
                if not chunk:
                    break
                out.extend(chunk)
                if len(out) > _MAX_DECOMPRESSED_BYTES:
                    log.warning(
                        "epss_feed_decompressed_too_large",
                        host=safe_host,
                        limit_bytes=_MAX_DECOMPRESSED_BYTES,
                    )
                    raise EpssFeedUnavailable(
                        f"EPSS feed exceeded {_MAX_DECOMPRESSED_BYTES} "
                        "decompressed bytes"
                    )
    except (OSError, EOFError, zlib.error) as exc:
        log.warning("epss_feed_not_gzip", host=safe_host, error=type(exc).__name__)
        raise EpssFeedUnavailable(
            f"EPSS feed body is not readable gzip: {type(exc).__name__}"
        ) from exc
    return io.StringIO(out.decode("utf-8", errors="replace"))


__all__ = [
    "EpssEntry",
    "EpssFeed",
    "EpssFeedUnavailable",
    "fetch_epss_scores",
    "parse_epss_csv",
]
