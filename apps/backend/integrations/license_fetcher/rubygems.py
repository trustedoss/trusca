"""
RubyGems license fetcher.

Resolves ``pkg:gem/<name>@<version>`` PURLs against the public RubyGems v2
API:

* ``GET /api/v2/rubygems/<name>/versions/<version>.json`` returns the
  per-version metadata, including a ``licenses`` array. Gem authors declare
  licenses as SPDX ids in the gemspec ``licenses =`` field, so the array is
  usually already SPDX (``["MIT"]``); we still normalize each entry through
  :func:`normalize_spdx_id` and take the first that maps.

RubyGems publishes a rate limit (https://guides.rubygems.org/rubygems-org-rate-limits/);
the shared ``request_with_retry`` helper honours per-host minimum intervals.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx
import structlog

from .base import (
    DEFAULT_TIMEOUT_SECONDS,
    USER_AGENT,
    LicenseFetchResult,
    normalize_spdx_id,
    request_with_retry,
)

log = structlog.get_logger("integrations.license_fetcher.rubygems")

_RUBYGEMS_HOST = "rubygems.org"
_RUBYGEMS_BASE = "https://rubygems.org/api/v2/rubygems"
_MIN_INTERVAL_SECONDS = 0.2


def _parse_purl(purl: str) -> tuple[str, str] | None:
    """Return ``(name, version)`` for a Gem PURL, or None.

    Accepts ``pkg:gem/<name>@<version>``. Gem names are a single segment
    (no namespace), case-sensitive on the lookup side, so the URL path is
    straightforward.
    """
    if not purl.startswith("pkg:gem/"):
        return None
    body = purl[len("pkg:gem/"):]
    for sep in ("?", "#"):
        if sep in body:
            body = body.split(sep, 1)[0]
    if "@" not in body:
        return None
    name, version = body.rsplit("@", 1)
    if not name or not version:
        return None
    return name, version


class RubyGemsLicenseFetcher:
    """Resolve RubyGems licenses via the v2 per-version endpoint."""

    source = "rubygems"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            # follow_redirects=False — matches the other fetchers (chore PR #6
            # L4): the v2 API terminates at rubygems.org; a 3xx would point at
            # an off-registry host we have not vetted.
            self._http = httpx.Client(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=timeout,
                follow_redirects=False,
            )
        return self._http

    def close(self) -> None:
        if self._owned and self._http is not None:
            self._http.close()
            self._http = None

    def fetch(
        self,
        purl: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LicenseFetchResult | None:
        parsed = _parse_purl(purl)
        if parsed is None:
            log.info("rubygems_purl_unrecognized", purl=purl)
            return None
        name, version = parsed
        url = f"{_RUBYGEMS_BASE}/{quote(name, safe='')}/versions/{quote(version, safe='')}.json"
        client = self._client(timeout)
        response = request_with_retry(
            client=client,
            method="GET",
            url=url,
            host=_RUBYGEMS_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None
        try:
            payload = response.json()
        except ValueError:
            log.warning("rubygems_invalid_json", name=name, version=version)
            return None
        if not isinstance(payload, dict):
            return None
        licenses = payload.get("licenses")
        # ``licenses`` is a JSON array of SPDX-ish strings (or null when the
        # gemspec omitted it). Take the first entry that normalizes to an SPDX
        # id — most gems declare exactly one.
        if not isinstance(licenses, list):
            return None
        for entry in licenses:
            if not isinstance(entry, str):
                continue
            spdx = normalize_spdx_id(entry)
            if spdx is not None:
                return LicenseFetchResult(
                    spdx_id=spdx,
                    reference_url=None,
                    source=self.source,
                )
        log.info(
            "rubygems_license_unmapped",
            name=name,
            version=version,
            licenses=str(licenses)[:120],
        )
        return None


__all__ = ["RubyGemsLicenseFetcher"]
