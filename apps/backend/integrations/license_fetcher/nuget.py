"""
NuGet license fetcher.

Resolves ``pkg:nuget/<name>@<version>`` PURLs against the public NuGet v3
registration API (JSON — we deliberately avoid the ``.nuspec`` XML, since
parsing untrusted network XML with the stdlib parser risks entity-expansion
DoS and the project does not vendor a hardened XML parser):

* ``GET /v3/registration5-semver1/<lower-id>/<lower-version>.json`` — the
  per-version registration leaf. Its ``catalogEntry`` (inline object or a URL
  to fetch) carries ``licenseExpression``, an SPDX expression, for packages
  that declared one with ``<license type="expression">`` in their nuspec.

Packages that only carry a legacy ``licenseUrl`` (a link, not an SPDX id) or a
``type="file"`` license cannot be resolved to an SPDX id and stay unknown —
that is the honest result, and the target for W8-#49 is "under 50% unknown",
not zero.

NuGet ids and versions are case-insensitive; the registration paths are
lowercase, so we lower-case both before building the URL.
"""

from __future__ import annotations

from typing import Any
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

log = structlog.get_logger("integrations.license_fetcher.nuget")

_NUGET_HOST = "api.nuget.org"
_NUGET_BASE = "https://api.nuget.org/v3/registration5-semver1"
_MIN_INTERVAL_SECONDS = 0.1


def _parse_purl(purl: str) -> tuple[str, str] | None:
    """Return ``(id, version)`` for a NuGet PURL, or None.

    Accepts ``pkg:nuget/<id>@<version>``. NuGet package ids have no namespace
    segment; the registration API path is lowercase, so the caller lowercases.
    """
    if not purl.startswith("pkg:nuget/"):
        return None
    body = purl[len("pkg:nuget/"):]
    for sep in ("?", "#"):
        if sep in body:
            body = body.split(sep, 1)[0]
    if "@" not in body:
        return None
    name, version = body.rsplit("@", 1)
    if not name or not version:
        return None
    return name, version


class NuGetLicenseFetcher:
    """Resolve NuGet licenses via the v3 registration API."""

    source = "nuget"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            # follow_redirects=False — matches the other fetchers (chore PR #6
            # L4). The registration + catalog endpoints both live under
            # api.nuget.org; we re-validate the host on the catalog hop below.
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

    def _license_expression(
        self, catalog_entry: Any, client: httpx.Client
    ) -> str | None:
        """Pull ``licenseExpression`` from a catalogEntry (inline dict or URL).

        registration5-semver1 sometimes inlines the catalogEntry object and
        sometimes gives a URL to it; handle both. When it's a URL we only
        follow it if it stays on ``api.nuget.org`` (no off-registry hop).
        """
        if isinstance(catalog_entry, dict):
            expr = catalog_entry.get("licenseExpression")
            return expr if isinstance(expr, str) and expr else None
        if not isinstance(catalog_entry, str) or not catalog_entry:
            return None
        if not catalog_entry.startswith("https://api.nuget.org/"):
            log.info("nuget_catalog_off_host", url=catalog_entry[:120])
            return None
        response = request_with_retry(
            client=client,
            method="GET",
            url=catalog_entry,
            host=_NUGET_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        expr = payload.get("licenseExpression")
        return expr if isinstance(expr, str) and expr else None

    def fetch(
        self,
        purl: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> LicenseFetchResult | None:
        parsed = _parse_purl(purl)
        if parsed is None:
            log.info("nuget_purl_unrecognized", purl=purl)
            return None
        name, version = parsed
        lower_id = name.lower()
        lower_version = version.lower()
        # safe="" so a hostile package name cannot introduce a path separator
        # and walk the registry path (in-registry traversal hardening).
        url = (
            f"{_NUGET_BASE}/{quote(lower_id, safe='')}/"
            f"{quote(lower_version, safe='')}.json"
        )
        client = self._client(timeout)
        response = request_with_retry(
            client=client,
            method="GET",
            url=url,
            host=_NUGET_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None
        try:
            payload = response.json()
        except ValueError:
            log.warning("nuget_invalid_json", name=name, version=version)
            return None
        if not isinstance(payload, dict):
            return None
        expression = self._license_expression(payload.get("catalogEntry"), client)
        if not expression:
            log.info("nuget_no_license_expression", name=name, version=version)
            return None
        spdx = normalize_spdx_id(expression)
        if spdx is None:
            log.info(
                "nuget_license_unmapped",
                name=name,
                version=version,
                expression=expression[:120],
            )
            return None
        return LicenseFetchResult(
            spdx_id=spdx,
            reference_url=None,
            source=self.source,
        )


__all__ = ["NuGetLicenseFetcher"]
