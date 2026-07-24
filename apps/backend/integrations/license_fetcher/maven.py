"""
Maven Central license fetcher.

Resolves ``pkg:maven/<group>/<artifact>@<version>`` PURLs against the
public Maven Central read endpoints:

* ``https://repo1.maven.org/maven2/<g>/<a>/<v>/<a>-<v>.pom`` — the
  authoritative POM. We parse the small ``<licenses>`` block out of
  the XML without pulling lxml; for our purposes a regex over the
  ``<name>`` element inside ``<licenses>`` is enough (Maven Central
  POMs have a stable enough structure that this is more reliable
  than wiring up XML namespace handling).
* ``https://search.maven.org/solrsearch/select?q=g:...+a:...+v:...&wt=json``
  is *not* used — it returns aggregated metadata without licenses.

We deliberately do not retry on 5xx with ``repo1`` because Maven
Central serves these files from a CDN; transient errors are rare and
the shared retry wrapper in :mod:`base` handles them.
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

log = structlog.get_logger("integrations.license_fetcher.maven")

_MAVEN_CENTRAL_HOST = "repo1.maven.org"
_MAVEN_CENTRAL_BASE = "https://repo1.maven.org/maven2"

# Maven Central does not publish a hard rate limit but the ASF infra
# guidance (https://infra.apache.org/) asks integrations to be
# considerate; 0.25s between calls is a safe default for our
# best-effort enrichment path.
_MIN_INTERVAL_SECONDS = 0.25


# Regexes are written against a ``<licenses>`` block of the form
#   <licenses>
#     <license>
#       <name>The Apache Software License, Version 2.0</name>
#       <url>http://...</url>
#     </license>
#   </licenses>
# We capture the first license entry — POMs occasionally list
# alternative-licenses (dual-licensing); the cdxgen path already skips
# compound expressions and so does this one.
_LICENSES_BLOCK_RE = re.compile(r"<licenses>(.*?)</licenses>", re.DOTALL | re.IGNORECASE)
_LICENSE_ENTRY_RE = re.compile(r"<license>(.*?)</license>", re.DOTALL | re.IGNORECASE)
_NAME_RE = re.compile(r"<name>\s*(.*?)\s*</name>", re.DOTALL | re.IGNORECASE)
_URL_RE = re.compile(r"<url>\s*(.*?)\s*</url>", re.DOTALL | re.IGNORECASE)


def _parse_purl(purl: str) -> tuple[str, str, str] | None:
    """Return ``(group, artifact, version)`` for a Maven PURL, or None.

    Accepts the canonical CycloneDX shape ``pkg:maven/<group>/<artifact>@<v>``
    where ``<group>`` may itself contain dots (``com.fasterxml.jackson``).
    Maven groups are case-sensitive; we preserve case verbatim.
    """
    if not purl.startswith("pkg:maven/"):
        return None
    body = purl[len("pkg:maven/"):]
    # Strip query/fragment if any (cdxgen sometimes emits ``?type=jar``).
    for sep in ("?", "#"):
        if sep in body:
            body = body.split(sep, 1)[0]
    if "@" not in body:
        return None
    coord, version = body.rsplit("@", 1)
    if "/" not in coord or not version:
        return None
    group, artifact = coord.rsplit("/", 1)
    if not group or not artifact:
        return None
    return group, artifact, version


def _parse_license_xml(xml: str) -> tuple[str, str | None] | None:
    """Return the first ``(name, url)`` pair in a ``<licenses>`` block."""
    block_match = _LICENSES_BLOCK_RE.search(xml)
    if block_match is None:
        return None
    block = block_match.group(1)
    entry_match = _LICENSE_ENTRY_RE.search(block)
    if entry_match is None:
        return None
    entry = entry_match.group(1)
    name_match = _NAME_RE.search(entry)
    if name_match is None:
        return None
    url_match = _URL_RE.search(entry)
    return (name_match.group(1), url_match.group(1) if url_match else None)


class MavenLicenseFetcher:
    """Resolve Maven Central licenses by fetching the POM."""

    source = "maven_central"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            # follow_redirects=False — security-reviewer L4 (chore PR #6).
            # Maven Central is a single authoritative origin, so a
            # legitimate response is never a 3xx; an unexpected redirect
            # would indicate a phishing host or registry mirror change
            # we have not vetted.
            self._http = httpx.Client(
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/xml, text/xml, text/plain, */*",
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
            log.info("maven_purl_unrecognized", purl=purl)
            return None
        group, artifact, version = parsed
        # ``safe=""`` on every path piece (W8-#49 security-reviewer follow-up):
        # the default ``safe='/'`` would let a hostile purl smuggle ``/`` (or
        # an encoded ``../``) through ``artifact`` / ``version`` and traverse
        # to an arbitrary in-registry path. The group segments are quoted
        # individually so the ``.``→``/`` expansion stays the ONLY source of
        # separators in the URL.
        group_path = "/".join(quote(seg, safe="") for seg in group.split("."))
        url = (
            f"{_MAVEN_CENTRAL_BASE}/"
            f"{group_path}/"
            f"{quote(artifact, safe='')}/"
            f"{quote(version, safe='')}/"
            f"{quote(artifact, safe='')}-{quote(version, safe='')}.pom"
        )
        client = self._client(timeout)
        response = request_with_retry(
            client=client,
            method="GET",
            url=url,
            host=_MAVEN_CENTRAL_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None
        parsed_license = _parse_license_xml(response.text)
        if parsed_license is None:
            log.info(
                "maven_pom_no_licenses",
                group=group,
                artifact=artifact,
                version=version,
            )
            return None
        name, ref_url = parsed_license
        spdx = normalize_spdx_id(name)
        if spdx is None:
            log.info(
                "maven_license_unmapped",
                group=group,
                artifact=artifact,
                version=version,
                name=name[:120],
            )
            return None
        # security-reviewer Medium #2 (chore PR #7) — POM ``<url>`` is
        # attacker-controlled metadata. Even with SPDX normalisation
        # succeeding, the URL itself is whatever the publisher put in
        # the POM and may point to a phishing or malware host. We drop
        # it here uniformly across all four fetchers; a follow-up PR
        # will land an SPDX id → spdx.org/licenses/<id>.html fallback
        # in ``LicenseDrawer.tsx`` so the frontend keeps a clickable
        # license link. Until then the licence panel renders without
        # an external link, which is strictly safer than a phishing
        # URL.
        del ref_url
        return LicenseFetchResult(
            spdx_id=spdx,
            reference_url=None,
            source=self.source,
        )


__all__ = ["MavenLicenseFetcher"]
