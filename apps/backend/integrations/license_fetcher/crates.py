"""
crates.io license fetcher.

Resolves ``pkg:cargo/<name>@<version>`` PURLs against the public
crates.io v1 API:

* ``GET /api/v1/crates/<name>/<version>`` returns the per-version
  metadata, including the canonical ``version.license`` field which
  carries an SPDX expression directly.

crates.io publishes a strict 1 req/sec rate limit
(https://crates.io/data-access). The shared ``request_with_retry``
helper already honours per-host minimum intervals; we set
``_MIN_INTERVAL_SECONDS = 1.0`` here so even a tight loop stays
within policy.
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

log = structlog.get_logger("integrations.license_fetcher.crates")

_CRATES_HOST = "crates.io"
_CRATES_BASE = "https://crates.io/api/v1/crates"
_MIN_INTERVAL_SECONDS = 1.0


def _parse_purl(purl: str) -> tuple[str, str] | None:
    """Return ``(name, version)`` for a Cargo PURL, or None.

    Accepts ``pkg:cargo/<name>@<version>``. Cargo crate names are
    lowercase ASCII (a-z, 0-9, -, _) with no namespace, so the URL
    path is straightforward.
    """
    if not purl.startswith("pkg:cargo/"):
        return None
    body = purl[len("pkg:cargo/"):]
    for sep in ("?", "#"):
        if sep in body:
            body = body.split(sep, 1)[0]
    if "@" not in body:
        return None
    name, version = body.rsplit("@", 1)
    if not name or not version:
        return None
    return name, version


class CratesLicenseFetcher:
    """Resolve crates.io licenses via the v1 versions endpoint."""

    source = "crates_io"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
                timeout=timeout,
                follow_redirects=True,
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
            log.info("crates_purl_unrecognized", purl=purl)
            return None
        name, version = parsed
        url = f"{_CRATES_BASE}/{quote(name)}/{quote(version)}"
        client = self._client(timeout)
        response = request_with_retry(
            client=client,
            method="GET",
            url=url,
            host=_CRATES_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None
        try:
            payload = response.json()
        except ValueError:
            log.warning("crates_invalid_json", name=name, version=version)
            return None
        version_block = payload.get("version") if isinstance(payload, dict) else None
        if not isinstance(version_block, dict):
            return None
        license_text = version_block.get("license")
        if not isinstance(license_text, str):
            return None
        spdx = normalize_spdx_id(license_text)
        if spdx is None:
            log.info(
                "crates_license_unmapped",
                name=name,
                version=version,
                license_text=license_text[:120],
            )
            return None
        return LicenseFetchResult(
            spdx_id=spdx,
            reference_url=None,
            source=self.source,
        )


__all__ = ["CratesLicenseFetcher"]
