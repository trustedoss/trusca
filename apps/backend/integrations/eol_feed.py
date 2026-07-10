"""
endoflife.date feed client — fresh EOL dataset for the weekly refresh beat.

Fetches ``https://endoflife.date/api/{product}.json`` for exactly the
products the vendored whitelist (``services/eol/eol_purl_map.json``)
references and assembles the same compact dataset shape the vendored
``eol_snapshot.json`` carries (``{"_snapshot": "<ISO date>", "<product>":
[{cycle, eol, ...}], ...}``) — so the refresh task can treat "fetched" and
"vendored" datasets interchangeably.

Structural mirror of :mod:`integrations.kev_feed` scaled down to many small
documents instead of one large one:

  * per-product byte ceiling (a product document is a few KB; 2 MiB flags a
    misconfigured/compromised mirror without buffering it),
  * per-product failures are SKIPPED and counted — one broken product must
    not discard the other nine (mirrors the per-entry skip posture),
  * a whole-run wall-clock budget bounds the tick even against a slow-drip
    server (the BomLens build-eol-index.py 60s budget),
  * :class:`EolFeedUnavailable` is raised only when NOTHING was fetched —
    the task treats it exactly like a KEV feed outage (skip, retry next
    tick, existing verdicts untouched).

Trust model: the URL template comes exclusively from operator env
(``EOL_FEED_URL_TEMPLATE``, read at call time) — no user write path, so no
``core.url_guard`` (the kev_feed convention). Product slugs come from the
vendored map, never from user input. Logs carry the host only, never the
full URL.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog

from core.config import eol_feed_url_template, eol_refresh_timeout_seconds

log = structlog.get_logger("integrations.eol_feed")

# Per-product response ceiling. Real documents are a few KB; anything near
# this is a broken or hostile mirror.
_MAX_PRODUCT_BYTES = 2 * 1024 * 1024

# Whole-run wall-clock budget (BomLens build-eol-index.py parity). Products
# past the deadline are skipped and counted as failed for this tick.
_RUN_BUDGET_SECONDS = 60

# Only the fields the evaluator (and the staleness surface) reads — keeps
# the persisted snapshot the same few-KB shape as the vendored file.
_KEEP_FIELDS = ("cycle", "eol", "releaseDate", "latest", "latestReleaseDate")

# Ceiling on cycles accepted per product — a real product has dozens.
_MAX_CYCLES_PER_PRODUCT = 500


class EolFeedUnavailable(Exception):
    """No product could be fetched at all (network dead, mirror broken).

    Per-product failures below this threshold are NOT this exception — they
    are skipped and surfaced in the result's ``failed`` list.
    """


@dataclass(frozen=True)
class EolFetchResult:
    """Outcome of one feed sweep.

    ``dataset`` is the compact snapshot dict (``_snapshot`` stamped with
    today's UTC date); ``fetched`` / ``failed`` list product slugs.
    """

    dataset: dict[str, Any]
    fetched: list[str]
    failed: list[str]


def _safe_host(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return "<invalid>"
    return parsed.hostname or "<no-host>"


def _compact_cycles(payload: Any) -> list[dict[str, Any]] | None:
    """Reduce one product document to the evaluator's field set.

    Returns ``None`` for a document that is not a list of objects — the
    product is counted failed rather than persisting garbage.
    """
    if not isinstance(payload, list):
        return None
    compact: list[dict[str, Any]] = []
    for entry in payload[:_MAX_CYCLES_PER_PRODUCT]:
        if not isinstance(entry, dict):
            continue
        compact.append({k: entry[k] for k in _KEEP_FIELDS if k in entry})
    return compact if compact else None


def _fetch_product(
    client: httpx.Client, url: str, safe_host: str
) -> list[dict[str, Any]] | None:
    """One bounded product fetch; ``None`` on any failure (counted, not raised)."""
    try:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                log.warning(
                    "eol_feed_http_error",
                    host=safe_host,
                    status=response.status_code,
                )
                return None
            buf = bytearray()
            for chunk in response.iter_bytes():
                buf.extend(chunk)
                if len(buf) > _MAX_PRODUCT_BYTES:
                    log.warning(
                        "eol_feed_product_too_large",
                        host=safe_host,
                        limit_bytes=_MAX_PRODUCT_BYTES,
                    )
                    return None
    except (
        httpx.TimeoutException,
        httpx.HTTPError,
        # kev_feed precedent: InvalidURL is not an HTTPError subclass, and a
        # schemeless URL surfaces as a bare ValueError on httpx 0.28 — both
        # would otherwise leak the full URL (possible mirror auth token) in
        # a crash. Type name only, host only.
        httpx.InvalidURL,
        httpx.UnsupportedProtocol,
        ValueError,
    ) as exc:
        log.warning(
            "eol_feed_network_failure",
            host=safe_host,
            error_type=type(exc).__name__,
        )
        return None
    try:
        payload = json.loads(bytes(buf))
    except (ValueError, UnicodeDecodeError):
        log.warning("eol_feed_invalid_json", host=safe_host)
        return None
    return _compact_cycles(payload)


def fetch_eol_dataset(
    products: list[str], *, http: httpx.Client | None = None
) -> EolFetchResult:
    """Fetch the compact dataset for ``products`` (the map's slugs).

    Raises :class:`EolFeedUnavailable` only when EVERY product failed;
    partial results return normally with the failures listed (the task's
    sanity floor decides whether they are usable).
    """
    template = eol_feed_url_template()
    safe_host = _safe_host(template.replace("{product}", "probe"))
    owned = http is None
    client = http or httpx.Client(
        timeout=eol_refresh_timeout_seconds(),
        follow_redirects=True,
    )
    dataset: dict[str, Any] = {
        "_snapshot": datetime.now(tz=UTC).date().isoformat()
    }
    fetched: list[str] = []
    failed: list[str] = []
    deadline = time.monotonic() + _RUN_BUDGET_SECONDS
    try:
        for product in products:
            if time.monotonic() > deadline:
                log.warning(
                    "eol_feed_budget_exhausted",
                    host=safe_host,
                    budget_seconds=_RUN_BUDGET_SECONDS,
                    remaining=len(products) - len(fetched) - len(failed),
                )
                failed.extend(
                    p for p in products if p not in fetched and p not in failed
                )
                break
            cycles = _fetch_product(
                client, template.replace("{product}", product), safe_host
            )
            if cycles is None:
                failed.append(product)
                continue
            dataset[product] = cycles
            fetched.append(product)
    finally:
        if owned:
            client.close()

    if not fetched:
        raise EolFeedUnavailable(
            f"no endoflife.date product could be fetched ({len(failed)} failed)"
        )
    log.info(
        "eol_feed_fetched",
        host=safe_host,
        fetched=len(fetched),
        failed=len(failed),
    )
    return EolFetchResult(dataset=dataset, fetched=fetched, failed=failed)


__all__ = [
    "EolFeedUnavailable",
    "EolFetchResult",
    "fetch_eol_dataset",
]
