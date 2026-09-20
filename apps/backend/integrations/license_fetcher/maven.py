# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
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

A POM that declares no ``<licenses>`` of its own inherits the licenses of its
``<parent>``, which Maven itself does when it builds the effective model. The
fetcher follows that chain (bounded by ``LICENSE_FETCH_MAVEN_PARENT_MAX_DEPTH``)
and records which ancestor the answer came from. A POM that declares a license
never triggers a parent lookup.

We deliberately do not retry on 5xx with ``repo1`` because Maven
Central serves these files from a CDN; transient errors are rare and
the shared retry wrapper in :mod:`base` handles them.
"""

from __future__ import annotations

import re
import time
from typing import Final, Protocol
from urllib.parse import quote

import httpx
import structlog

from core.config import license_fetch_maven_parent_max_depth

from .base import (
    DEFAULT_TIMEOUT_SECONDS,
    USER_AGENT,
    LicenseFetchResult,
    normalize_spdx_id,
    request_with_retry,
)
from .budget import active_budget

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


_NOISE_RE = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|<!DOCTYPE[^>]*>", re.DOTALL | re.IGNORECASE
)
_TAG_RE = re.compile(r"<(/?)([A-Za-z_][\w.\-]*)(?:\s[^>]*?)?(/?)>")
_COORDINATE_PART_RE = re.compile(r"\A[A-Za-z0-9_.\-]+\Z")

#: ``license_fetch_cache.source`` for a result that came from an ancestor POM.
SOURCE_PARENT: Final = "maven_central_parent"


def _project_children(xml: str) -> dict[str, str]:
    """Inner text of each direct child of ``<project>``; the first one wins.

    ``<licenses>`` and ``<parent>`` only mean something as direct children of
    the project. The same names turn up inside ``<profiles>``, plugin
    configuration and comments, and a search over the whole document reads
    those as the project's own license. Nothing else about the POM is needed,
    so this is a tag stack rather than an XML parser (see the module docstring
    on why the fetcher avoids one).
    """
    clean = _NOISE_RE.sub("", xml)
    children: dict[str, str] = {}
    stack: list[tuple[str, int]] = []  # (element name, index just past its start tag)
    for match in _TAG_RE.finditer(clean):
        closing, name, self_closing = match.groups()
        if closing:
            names = [entry[0] for entry in stack]
            if name not in names:
                continue
            index = len(names) - 1 - names[::-1].index(name)
            _, inner_start = stack[index]
            del stack[index:]
            if index == 1:
                children.setdefault(name, clean[inner_start : match.start()])
            continue
        if not stack and name != "project":
            return {}
        if self_closing:
            if len(stack) == 1:
                children.setdefault(name, "")
            continue
        stack.append((name, match.end()))
    return children


def _parse_license_xml(xml: str) -> tuple[str, str | None] | None:
    """Return the first ``(name, url)`` pair in the project's ``<licenses>``."""
    block = _project_children(xml).get("licenses")
    if block is None:
        return None
    entry_match = _LICENSE_ENTRY_RE.search(block)
    if entry_match is None:
        return None
    entry = entry_match.group(1)
    name_match = _NAME_RE.search(entry)
    if name_match is None:
        return None
    url_match = _URL_RE.search(entry)
    return (name_match.group(1), url_match.group(1) if url_match else None)


def _parse_parent(xml: str) -> tuple[str, str, str] | None:
    """The ``<parent>`` coordinates, or ``None`` when there is none to follow.

    A coordinate that is not a plain literal (``${revision}``, a version range,
    anything outside the characters Maven allows) is not resolved: the value
    would need the child's properties, which are not read here, and a guessed
    parent would attribute someone else's license to the component.
    """
    block = _project_children(xml).get("parent")
    if block is None:
        return None
    parts: list[str] = []
    for tag in ("groupId", "artifactId", "version"):
        found = re.search(rf"<{tag}>\s*(.*?)\s*</{tag}>", block, re.DOTALL)
        if found is None or _COORDINATE_PART_RE.match(found.group(1)) is None:
            log.info("maven_parent_unresolvable", field=tag)
            return None
        parts.append(found.group(1))
    return parts[0], parts[1], parts[2]


def _pom_url(group: str, artifact: str, version: str) -> str:
    # ``safe=""`` on every path piece (W8-#49 security review follow-up):
    # the default ``safe='/'`` would let a hostile purl smuggle ``/`` (or
    # an encoded ``../``) through ``artifact`` / ``version`` and traverse
    # to an arbitrary in-registry path. The group segments are quoted
    # individually so the ``.``→``/`` expansion stays the ONLY source of
    # separators in the URL.
    group_path = "/".join(quote(seg, safe="") for seg in group.split("."))
    return (
        f"{_MAVEN_CENTRAL_BASE}/"
        f"{group_path}/"
        f"{quote(artifact, safe='')}/"
        f"{quote(version, safe='')}/"
        f"{quote(artifact, safe='')}-{quote(version, safe='')}.pom"
    )


def _coordinate(group: str, artifact: str, version: str) -> str:
    return f"{group}:{artifact}:{version}"


def _ancestor_purl(group: str, artifact: str, version: str) -> str:
    return f"pkg:maven/{group}/{artifact}@{version}"


class AncestorCache(Protocol):
    """Where a parent POM's answer is remembered so its children share it."""

    def lookup(self, purl: str) -> tuple[bool, LicenseFetchResult | None]: ...

    def store(self, purl: str, result: LicenseFetchResult) -> None: ...


class MavenLicenseFetcher:
    """Resolve Maven Central licenses by fetching the POM."""

    source = "maven_central"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None
        #: Set by the dispatcher; without it every chain is walked over the wire.
        self.ancestor_cache: AncestorCache | None = None
        #: True when the last :meth:`fetch` stopped for a reason that says
        #: nothing about the package (budget spent, breaker open), so its
        #: ``None`` must not be cached as a confirmed miss.
        self.lookup_incomplete = False

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            # follow_redirects=False — security review finding.
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
        self.lookup_incomplete = False
        group, artifact, version = parsed
        client = self._client(timeout)
        max_depth = license_fetch_maven_parent_max_depth()
        visited = {(group, artifact, version)}
        hops: list[tuple[str, str, str]] = []  # ancestors read, nearest first
        current = (group, artifact, version)
        started = time.monotonic()
        text = self._read_pom(client, current)
        while text is not None:
            parsed_license = _parse_license_xml(text)
            if parsed_license is not None:
                return self._conclude(
                    parsed_license, root=(group, artifact, version), origin=current, hops=hops
                )
            parent = _parse_parent(text)
            if parent is None:
                break
            if parent in visited:
                log.info("maven_parent_cycle", group=group, artifact=artifact, version=version)
                return None
            if len(hops) >= max_depth:
                log.info(
                    "maven_parent_depth_limit",
                    group=group,
                    artifact=artifact,
                    version=version,
                    max_depth=max_depth,
                )
                return None
            visited.add(parent)
            cached = self._cached_ancestor(parent)
            if cached is not None:
                hit, hit_result = cached
                if not hit or hit_result is None:
                    return None
                return self._inherit(
                    hit_result.spdx_id,
                    origin=hit_result.inherited_from or _coordinate(*parent),
                    hops=[*hops, parent],
                    cached=True,
                )
            budget = active_budget()
            if budget is not None:
                reason = budget.refusal(time.monotonic() - started)
                if reason is not None:
                    budget.note_skipped(reason)
                    self.lookup_incomplete = True
                    return None
            hops.append(parent)
            current = parent
            text = self._read_pom(client, current)
        log.info("maven_pom_no_licenses", group=group, artifact=artifact, version=version)
        return None

    def _read_pom(self, client: httpx.Client, coords: tuple[str, str, str]) -> str | None:
        response = request_with_retry(
            client=client,
            method="GET",
            url=_pom_url(*coords),
            host=_MAVEN_CENTRAL_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        return None if response is None else response.text

    def _cached_ancestor(
        self, coords: tuple[str, str, str]
    ) -> tuple[bool, LicenseFetchResult | None] | None:
        if self.ancestor_cache is None:
            return None
        hit, result = self.ancestor_cache.lookup(_ancestor_purl(*coords))
        return (hit, result) if hit else None

    def _conclude(
        self,
        declared: tuple[str, str | None],
        *,
        root: tuple[str, str, str],
        origin: tuple[str, str, str],
        hops: list[tuple[str, str, str]],
    ) -> LicenseFetchResult | None:
        name, ref_url = declared
        spdx = normalize_spdx_id(name)
        if spdx is None:
            log.info(
                "maven_license_unmapped",
                group=origin[0],
                artifact=origin[1],
                version=origin[2],
                name=name[:120],
            )
            return None
        # security review, medium severity — POM ``<url>`` is
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
        if origin == root:
            return LicenseFetchResult(spdx_id=spdx, reference_url=None, source=self.source)
        return self._inherit(spdx, origin=_coordinate(*origin), hops=hops, cached=False)

    def _inherit(
        self, spdx: str, *, origin: str, hops: list[tuple[str, str, str]], cached: bool
    ) -> LicenseFetchResult:
        """The answer for a POM that took *spdx* from the ancestor *origin*.

        Every ancestor on the way is remembered with the same answer, so the
        next child of the same parent finds it without a request. Ancestors
        that came out of the cache already are not written back.
        """
        if self.ancestor_cache is not None:
            for hop in hops[: len(hops) - 1 if cached else len(hops)]:
                self.ancestor_cache.store(
                    _ancestor_purl(*hop),
                    LicenseFetchResult(
                        spdx_id=spdx,
                        reference_url=None,
                        source=self.source if _coordinate(*hop) == origin else SOURCE_PARENT,
                        inherited_from=None if _coordinate(*hop) == origin else origin,
                    ),
                )
        return LicenseFetchResult(
            spdx_id=spdx, reference_url=None, source=SOURCE_PARENT, inherited_from=origin
        )


__all__ = ["MavenLicenseFetcher"]
