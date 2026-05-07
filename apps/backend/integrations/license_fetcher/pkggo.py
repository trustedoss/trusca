"""
pkg.go.dev license fetcher.

Resolves ``pkg:golang/<module>@<version>`` PURLs by hitting
``https://pkg.go.dev/<module>@<version>?tab=licenses``. Unlike PyPI /
crates.io / Maven Central, the Go module ecosystem has no public JSON
license endpoint — pkg.go.dev exposes the info as rendered HTML.

We scrape conservatively:
  * Look for the ``<div data-test-id="UnitMeta-license">`` block (or
    legacy ``<div class="UnitMeta-license">``) that pkg.go.dev's
    template emits. Inside it, the first ``<a>`` element with an
    ``href`` of ``#lic-0``, ``#lic-1``, ... contains the SPDX-ish
    license name as its text node.
  * Fall back to a generic regex over ``"License-Name"`` JSON-LD if
    the template ever changes.
  * Reject compound expressions (``MIT AND BSD-3-Clause``) — same
    policy as the cdxgen extractor and the other fetchers.

This is best-effort: pkg.go.dev's HTML changes occasionally and we'd
rather emit ``None`` (license unknown, negative-cached for 24h) than
mis-classify a component. The UAT matrix already notes pkg.go.dev's
metadata limit (Go modules without a top-level LICENSE file are
intrinsically unknown to the proxy).
"""

from __future__ import annotations

import re
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

log = structlog.get_logger("integrations.license_fetcher.pkggo")

_PKG_GO_HOST = "pkg.go.dev"
_PKG_GO_BASE = "https://pkg.go.dev"
_MIN_INTERVAL_SECONDS = 0.25

# Primary signal — pkg.go.dev includes the parsed SPDX expression in a
# meta block tagged ``UnitMeta-license``. The matching div carries
# ``data-test-id="UnitMeta-license"`` and inside it the first
# anchor whose href ends with ``#lic-...`` carries the license name.
# The href may be a full path (``?tab=licenses#lic-0``) or a bare
# fragment (``#lic-0``) depending on which page rendered the snippet.
_LICENSE_ANCHOR_RE = re.compile(
    r'data-test-id="UnitMeta-license".*?<a[^>]*href="[^"]*#lic-\d+"[^>]*>\s*([^<\s][^<]*?)\s*</a>',
    re.DOTALL,
)
# Fallback — older / future templates may bury the value differently;
# this captures the first ``<a>`` inside any ``UnitMeta-license`` block.
_LICENSE_FALLBACK_RE = re.compile(
    r'class="[^"]*UnitMeta-license[^"]*".*?<a[^>]*>\s*([^<\s][^<]*?)\s*</a>',
    re.DOTALL,
)


def _parse_purl(purl: str) -> tuple[str, str] | None:
    """Return ``(module_path, version)`` for a Go PURL, or None.

    Accepts ``pkg:golang/<module>@<version>`` where ``<module>`` may
    contain forward slashes (``github.com/spf13/cobra``). cdxgen
    encodes the module path verbatim; we URL-encode each segment when
    building the pkg.go.dev URL.
    """
    if not purl.startswith("pkg:golang/"):
        return None
    body = purl[len("pkg:golang/"):]
    for sep in ("?", "#"):
        if sep in body:
            body = body.split(sep, 1)[0]
    if "@" not in body:
        return None
    module, version = body.rsplit("@", 1)
    if not module or not version:
        return None
    return module, version


def _extract_license_name(html: str) -> str | None:
    """Pull the license name out of a pkg.go.dev license-tab response."""
    m = _LICENSE_ANCHOR_RE.search(html)
    if m is None:
        m = _LICENSE_FALLBACK_RE.search(html)
    if m is None:
        return None
    candidate = m.group(1).strip()
    if not candidate:
        return None
    return candidate


class PkgGoLicenseFetcher:
    """Resolve Go module licenses by scraping pkg.go.dev."""

    source = "pkg_go_dev"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
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
            log.info("pkggo_purl_unrecognized", purl=purl)
            return None
        module, version = parsed
        # Each path segment is url-encoded individually so module slashes survive.
        module_path = "/".join(quote(seg, safe="") for seg in module.split("/"))
        url = f"{_PKG_GO_BASE}/{module_path}@{quote(version)}?tab=licenses"
        client = self._client(timeout)
        response = request_with_retry(
            client=client,
            method="GET",
            url=url,
            host=_PKG_GO_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None
        license_name = _extract_license_name(response.text)
        if license_name is None:
            log.info(
                "pkggo_license_block_missing",
                module=module,
                version=version,
            )
            return None
        spdx = normalize_spdx_id(license_name)
        if spdx is None:
            log.info(
                "pkggo_license_unmapped",
                module=module,
                version=version,
                license_name=license_name[:120],
            )
            return None
        return LicenseFetchResult(
            spdx_id=spdx,
            reference_url=f"{_PKG_GO_BASE}/{module_path}@{quote(version)}?tab=licenses",
            source=self.source,
        )


__all__ = ["PkgGoLicenseFetcher"]
