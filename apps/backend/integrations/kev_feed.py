"""
CISA KEV (Known Exploited Vulnerabilities) catalog feed client.

Downloads and parses the public CISA KEV JSON feed
(https://www.cisa.gov/known-exploited-vulnerabilities-catalog) into a
``{CVE-ID → KevEntry}`` mapping the daily refresh task
(``tasks.kev_catalog_refresh``) applies onto the ``vulnerabilities`` catalog
columns added by migration 0034 (``kev`` / ``kev_date_added`` /
``kev_due_date``).

Feed shape (stable since 2021)::

    {
      "title": "CISA Catalog of Known Exploited Vulnerabilities",
      "catalogVersion": "2026.07.01",
      "count": 1612,
      "vulnerabilities": [
        {
          "cveID": "CVE-2021-44228",
          "dateAdded": "2021-12-10",
          "dueDate": "2021-12-24",
          ...
        },
        ...
      ]
    }

Trust model / URL guard:
    The feed URL comes exclusively from operator env configuration
    (``KEV_FEED_URL``, read at call time per CLAUDE.md core rule #11). There
    is NO user-supplied write path to it, so it deliberately does NOT route
    through ``core.url_guard`` — the same convention as the env-only
    notification webhook URLs (``notifications/slack.py``). An operator who
    can set worker env vars already controls the process.

Adversarial-input posture:
    The feed is third-party JSON crossing a trust boundary, so parsing is
    defensive end to end: a response over the byte ceiling, non-JSON bytes,
    or a top-level shape that is not ``{vulnerabilities: [...]}`` raise
    :class:`KevFeedUnavailable` (the refresh task catches it and skips the
    tick — it never crashes the beat). Per-entry defects (non-dict entries,
    missing/blank ``cveID``, unparseable dates) are SKIPPED item-by-item with
    a summary WARNING, never raised — one malformed row in a 1600-row catalog
    must not discard the other 1599.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from core.config import kev_feed_url, kev_refresh_timeout_seconds

log = structlog.get_logger("integrations.kev_feed")

# Hard ceiling on the downloaded feed size. The real catalog is ~10 MiB as of
# 2026; 50 MiB leaves generous growth headroom while capping what a
# misconfigured mirror (or a compromised one serving garbage) can force the
# worker to buffer.
_MAX_FEED_BYTES = 50 * 1024 * 1024

# Total wall-clock deadline for the download (security-reviewer MINOR-1).
# ``KEV_REFRESH_TIMEOUT_SECONDS`` is httpx's PER-OPERATION timeout (connect /
# read / write each), so a hostile or broken server can slow-drip one chunk
# per read-timeout window and hold the worker far past the operator's intent
# without ever tripping the per-operation timeout. This monotonic deadline
# bounds the WHOLE transfer: 300s is 10× the default per-operation timeout —
# generous for a ~10 MiB CDN document, short enough that a drip attack cannot
# occupy a worker slot across beat ticks.
_FETCH_DEADLINE_SECONDS = 300

# Ceiling on the number of entries accepted per feed document
# (security-reviewer MINOR-2). The refresh task feeds the parsed keys into a
# single SQL ``IN`` clause — Postgres' extended query protocol caps bind
# parameters at 65,535 per statement (Int16 wire field), so a document at or
# above that would fail the statement outright, and a multi-million-entry
# document is a memory-blowup vector regardless. 50,000 keeps clear headroom
# under the bind limit while being ~30× the real catalog (~1,600 entries
# accumulated since 2021) — no legitimate feed approaches it.
_MAX_FEED_ENTRIES = 50_000

# Per-entry sanity cap on the CVE id length — ``vulnerabilities.external_id``
# is String(64), and no real CVE/GHSA id approaches that. Longer values are
# junk and are skipped rather than truncated (a truncated id would silently
# match nothing).
_MAX_CVE_ID_LEN = 64


class KevFeedUnavailable(Exception):
    """The KEV feed could not be fetched or parsed at the document level.

    Raised for network failures, non-2xx responses, oversized bodies,
    non-JSON payloads, and a top-level shape without a ``vulnerabilities``
    list. Callers (the refresh task) treat this as "skip this tick and retry
    on the next beat"; it is never a data-corruption signal.
    """


@dataclass(frozen=True)
class KevEntry:
    """One CVE's KEV listing — the two date fields the catalog persists.

    ``date_added``: the day CISA listed the CVE (feed ``dateAdded``).
    ``due_date``: CISA's remediation due date (feed ``dueDate``) — kept for
    the SLA follow-up feature. Either is ``None`` when the feed field was
    missing or unparseable (the listing itself still counts as KEV=true).
    """

    date_added: date | None
    due_date: date | None


def _safe_host(url: str) -> str:
    """Return only the host component for logging — never the full URL."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid>"
    return parsed.hostname or "<no-host>"


def _parse_feed_date(raw: Any) -> date | None:
    """Parse a feed date field (``YYYY-MM-DD``) to :class:`datetime.date`.

    Returns ``None`` for anything that is not a clean ISO-8601 calendar date
    — the entry survives with the date field unset rather than being dropped.
    """
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def parse_kev_catalog(payload: Any) -> dict[str, KevEntry]:
    """Parse a decoded KEV feed document into ``{CVE-ID → KevEntry}``.

    Keys are upper-cased ``cveID`` strings so lookups against our catalog's
    ``external_id`` are case-insensitive on the feed side. Entry-level
    defects are skipped (counted + summarised in ONE warning log, per §5
    "no log spam"); a document-level shape defect raises
    :class:`KevFeedUnavailable`.
    """
    if not isinstance(payload, dict):
        raise KevFeedUnavailable(
            f"KEV feed top-level shape is {type(payload).__name__}, expected object"
        )
    raw_entries = payload.get("vulnerabilities")
    if not isinstance(raw_entries, list):
        raise KevFeedUnavailable(
            "KEV feed 'vulnerabilities' field is missing or not an array"
        )
    if len(raw_entries) > _MAX_FEED_ENTRIES:
        # Document-level defect, not a per-entry one: truncating would
        # silently change delist semantics (entries past the cap would look
        # "removed"), so the whole document is refused. See the constant's
        # rationale (Postgres bind-parameter limit + memory).
        raise KevFeedUnavailable(
            f"KEV feed carries {len(raw_entries)} entries, over the "
            f"{_MAX_FEED_ENTRIES} ceiling"
        )

    catalog: dict[str, KevEntry] = {}
    skipped = 0
    for entry in raw_entries:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        cve_id = entry.get("cveID")
        if (
            not isinstance(cve_id, str)
            or not cve_id.strip()
            or len(cve_id) > _MAX_CVE_ID_LEN
        ):
            skipped += 1
            continue
        catalog[cve_id.strip().upper()] = KevEntry(
            date_added=_parse_feed_date(entry.get("dateAdded")),
            due_date=_parse_feed_date(entry.get("dueDate")),
        )

    if skipped:
        # One summary WARNING for the whole pass — per-entry logging on a
        # hostile feed would be its own log-flood vector.
        log.warning(
            "kev_feed_entries_skipped",
            skipped=skipped,
            parsed=len(catalog),
        )
    return catalog


def fetch_kev_catalog(*, http: httpx.Client | None = None) -> dict[str, KevEntry]:
    """Download and parse the CISA KEV catalog.

    Args:
        http: Optional pre-built client (tests inject an
            ``httpx.MockTransport``-backed one). When ``None`` a short-lived
            client is created with ``KEV_REFRESH_TIMEOUT_SECONDS`` and closed
            before returning. ``follow_redirects=True`` is safe here because
            the URL is env-only (see module docstring) and cisa.gov fronts
            the feed with a redirecting CDN.

    Returns:
        ``{CVE-ID (upper) → KevEntry}``.

    Raises:
        KevFeedUnavailable: network failure / timeout, non-2xx status,
            body over the 50 MiB ceiling, transfer over the wall-clock
            deadline, non-JSON payload, a top-level shape without a
            ``vulnerabilities`` array, or an entry count over
            ``_MAX_FEED_ENTRIES``.
    """
    url = kev_feed_url()
    safe_host = _safe_host(url)
    if url.strip().lower().startswith("http://"):
        # security-reviewer INFO — a plaintext mirror is an operator choice
        # (air-gapped internal endpoints exist), but flag it once per fetch
        # so a typo'd scheme on the public internet doesn't go unnoticed.
        # Host only; never the full URL.
        log.warning("kev_feed_insecure_scheme", host=safe_host)
    owned = http is None
    client = http or httpx.Client(
        timeout=kev_refresh_timeout_seconds(),
        follow_redirects=True,
    )
    started = time.monotonic()
    try:
        try:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    log.warning(
                        "kev_feed_http_error",
                        host=safe_host,
                        status=response.status_code,
                    )
                    raise KevFeedUnavailable(
                        f"KEV feed returned HTTP {response.status_code}"
                    )
                # Stream with a running byte cap so a hostile/misconfigured
                # mirror cannot make the worker buffer an unbounded body. The
                # Content-Length header alone is not trusted (it can lie).
                buf = bytearray()
                for chunk in response.iter_bytes():
                    # Whole-transfer wall-clock deadline — the per-operation
                    # httpx timeout does not bound a slow-drip transfer (each
                    # chunk resets the read timer). See _FETCH_DEADLINE_SECONDS.
                    if time.monotonic() - started > _FETCH_DEADLINE_SECONDS:
                        log.warning(
                            "kev_feed_deadline_exceeded",
                            host=safe_host,
                            deadline_seconds=_FETCH_DEADLINE_SECONDS,
                        )
                        raise KevFeedUnavailable(
                            f"KEV feed transfer exceeded "
                            f"{_FETCH_DEADLINE_SECONDS}s wall-clock deadline"
                        )
                    buf.extend(chunk)
                    if len(buf) > _MAX_FEED_BYTES:
                        log.warning(
                            "kev_feed_too_large",
                            host=safe_host,
                            limit_bytes=_MAX_FEED_BYTES,
                        )
                        raise KevFeedUnavailable(
                            f"KEV feed exceeded {_MAX_FEED_BYTES} bytes"
                        )
        except (
            httpx.TimeoutException,
            httpx.HTTPError,
            # security-reviewer INFO — httpx.InvalidURL is NOT an HTTPError
            # subclass (it inherits Exception directly) and would otherwise
            # bypass this handler and crash the caller with an exception whose
            # str() embeds the full URL (which may carry a mirror auth token).
            # httpx.UnsupportedProtocol IS an HTTPError subclass on current
            # httpx but is listed explicitly so a future hierarchy shuffle
            # cannot silently reopen the gap.
            httpx.InvalidURL,
            httpx.UnsupportedProtocol,
            # Observed on httpx 0.28: a schemeless KEV_FEED_URL (e.g. a typo
            # like "://host/kev.json") surfaces as a BARE ValueError from the
            # client's send path — same bypass class, same URL-leak risk.
            # The try block only wraps the stream/read calls, so this cannot
            # mask an unrelated ValueError from our own parsing code.
            ValueError,
        ) as exc:
            # Log/raise the exception TYPE only — httpx error strings embed
            # the request URL, and KEV_FEED_URL may point at an authenticated
            # internal mirror.
            log.warning(
                "kev_feed_network_failure",
                host=safe_host,
                error_type=type(exc).__name__,
            )
            raise KevFeedUnavailable(
                f"KEV feed network failure: {type(exc).__name__}"
            ) from exc

        try:
            payload = json.loads(bytes(buf))
        except (ValueError, UnicodeDecodeError) as exc:
            log.warning(
                "kev_feed_invalid_json",
                host=safe_host,
                error=str(exc)[:200],
            )
            raise KevFeedUnavailable("KEV feed body is not valid JSON") from exc

        catalog = parse_kev_catalog(payload)
        log.info(
            "kev_feed_fetched",
            host=safe_host,
            entry_count=len(catalog),
            bytes=len(buf),
        )
        return catalog
    finally:
        if owned:
            client.close()


__all__ = [
    "KevEntry",
    "KevFeedUnavailable",
    "fetch_kev_catalog",
    "parse_kev_catalog",
]
