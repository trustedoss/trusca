# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
ClearlyDefined licence fetcher — the fallback under the registry adapters (S5-A).

Why a fallback rather than a seventh registry adapter: ClearlyDefined does not
own any ecosystem. It is a curation layer over harvested scan output for ~55
million package versions across every ecosystem the registry adapters cover and
several they do not. Asking it first would put a hop in front of six adapters
that already answer authoritatively; asking it last turns "no licence found"
into "no licence found anywhere we know to look".

What it adds that the registry adapters cannot
----------------------------------------------
**npm.** ``PURL_PREFIX_TO_FETCHER`` has no ``pkg:npm/`` entry, so every npm
component has been going straight to a negative cache entry stamped
``unsupported_ecosystem`` without a single HTTP call. This is the first
coverage npm gets.

**Attributions.** ClearlyDefined harvests per-file copyright holders. The
NOTICE renderer already has a slot for them — it prints
``Copyright: <holders>`` per component and falls back to "holders not captured
in SBOM" when the SBOM carried none — so those attributions land in an existing
column rather than needing a new one.

Coordinates, not purls
----------------------
ClearlyDefined addresses packages by ``type/provider/namespace/name/revision``
rather than by purl. The mapping is fixed and small, so it lives here as a
table; a purl type absent from it is skipped rather than guessed at, because a
wrong coordinate returns someone else's licence rather than nothing.

Licence policy: the API is public, unauthenticated, and its curated data is
CC0. Rate limit is 250 requests/minute on the definitions routes, which the
shared host throttle keeps us well under.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from integrations.license_fetcher.base import (
    DEFAULT_TIMEOUT_SECONDS,
    USER_AGENT,
    LicenseFetchResult,
    normalize_spdx_id,
    request_with_retry,
)

log = structlog.get_logger("license_fetcher.clearlydefined")

_HOST = "api.clearlydefined.io"
_BASE = f"https://{_HOST}/definitions"
#: 250 req/min on the definitions routes → 0.25s keeps a wide margin, and this
#: only ever runs after six adapters have already declined.
_MIN_INTERVAL_SECONDS = 0.25

#: purl type → (ClearlyDefined type, provider). A purl type missing here is
#: skipped: guessing a provider returns a different package's licence, which is
#: worse than returning nothing.
_PURL_TYPE_TO_COORDINATES: dict[str, tuple[str, str]] = {
    "npm": ("npm", "npmjs"),
    "pypi": ("pypi", "pypi"),
    "maven": ("maven", "mavencentral"),
    "cargo": ("crate", "cratesio"),
    "nuget": ("nuget", "nuget"),
    "gem": ("gem", "rubygems"),
    "golang": ("go", "golang"),
    "composer": ("composer", "packagist"),
    "cocoapods": ("pod", "cocoapods"),
    "deb": ("deb", "debian"),
}

#: ClearlyDefined's "we looked and found nothing" marker. Treated as no answer
#: rather than as a licence id.
_NOASSERTION = "noassertion"


def parse_purl(purl: str) -> tuple[str, str, str, str] | None:
    """``pkg:npm/@scope/name@1.2.3`` → ``(type, namespace, name, revision)``.

    Returns ``None`` when the purl is unparseable, carries no version, or names
    a type with no coordinate mapping. A namespace-less package yields ``"-"``,
    which is the literal ClearlyDefined uses in that position.
    """
    if not purl or not purl.startswith("pkg:"):
        return None
    body = purl[len("pkg:") :]
    # Qualifiers and subpaths carry no coordinate information.
    body = body.split("?", 1)[0].split("#", 1)[0]
    if "/" not in body:
        return None

    purl_type, remainder = body.split("/", 1)
    purl_type = purl_type.strip().lower()
    if purl_type not in _PURL_TYPE_TO_COORDINATES:
        return None

    if "@" not in remainder:
        # ClearlyDefined addresses a specific revision; without one there is
        # nothing to ask for.
        return None
    name_part, revision = remainder.rsplit("@", 1)
    if not revision.strip():
        return None

    # Everything before the final segment is the namespace: `@scope` for npm,
    # `group.id` for maven, `owner` for go.
    if "/" in name_part:
        namespace, name = name_part.rsplit("/", 1)
    else:
        namespace, name = "-", name_part

    if not name.strip():
        return None
    return purl_type, namespace, name, revision


def _definition_url(purl: str) -> str | None:
    parsed = parse_purl(purl)
    if parsed is None:
        return None
    purl_type, namespace, name, revision = parsed
    cd_type, provider = _PURL_TYPE_TO_COORDINATES[purl_type]
    parts = [cd_type, provider, namespace, name, revision]
    # Each segment is escaped individually so a '/' inside a namespace cannot
    # invent an extra path element.
    return f"{_BASE}/" + "/".join(quote(part, safe="") for part in parts)


def extract_declared_license(payload: dict[str, Any]) -> str | None:
    """The definition's declared licence, normalised to an SPDX id.

    ``licensed.declared`` is ClearlyDefined's own conclusion. The discovered
    expressions underneath it are per-file scanner output, which is noisier and
    frequently disagrees with itself across a repository, so this reads only
    the declared field. ``NOASSERTION`` means "looked, found nothing".
    """
    licensed = payload.get("licensed")
    if not isinstance(licensed, dict):
        return None
    declared = licensed.get("declared")
    if not isinstance(declared, str) or not declared.strip():
        return None
    if declared.strip().lower() == _NOASSERTION:
        return None
    return normalize_spdx_id(declared)


def extract_attributions(payload: dict[str, Any], *, limit: int = 20) -> list[str]:
    """Copyright holders the harvest attributed to this package version.

    Capped because a large repository can carry hundreds of distinct holders,
    and the NOTICE column that receives them is itself clamped — sending an
    unbounded list would only be truncated further downstream.
    """
    licensed = payload.get("licensed")
    if not isinstance(licensed, dict):
        return []
    facets = licensed.get("facets")
    if not isinstance(facets, dict):
        return []
    core = facets.get("core")
    if not isinstance(core, dict):
        return []
    attribution = core.get("attribution")
    if not isinstance(attribution, dict):
        return []
    parties = attribution.get("parties")
    if not isinstance(parties, list):
        return []

    out: list[str] = []
    seen: set[str] = set()
    for party in parties:
        if not isinstance(party, str):
            continue
        cleaned = party.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= limit:
            break
    return out


class ClearlyDefinedLicenseFetcher:
    """Fallback fetcher, consulted after every registry adapter has declined."""

    source = "clearlydefined"

    def __init__(self, *, http: httpx.Client | None = None) -> None:
        self._http = http
        self._owned = http is None
        #: Attributions from the most recent successful fetch. Read by the
        #: dispatcher; not part of :class:`LicenseFetchResult`, which every
        #: other adapter shares and none of the others can populate.
        self.last_attributions: list[str] = []

    def _client(self, timeout: float) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
        return self._http

    def close(self) -> None:
        if self._owned and self._http is not None:
            self._http.close()
            self._http = None

    def fetch(
        self, purl: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> LicenseFetchResult | None:
        self.last_attributions = []
        url = _definition_url(purl)
        if url is None:
            log.info("clearlydefined_purl_unrecognized", purl=purl)
            return None

        response = request_with_retry(
            client=self._client(timeout),
            method="GET",
            url=url,
            host=_HOST,
            min_interval_seconds=_MIN_INTERVAL_SECONDS,
        )
        if response is None:
            return None

        try:
            payload = json.loads(response.text)
        except (ValueError, json.JSONDecodeError):
            log.warning("clearlydefined_bad_json", purl=purl)
            return None
        if not isinstance(payload, dict):
            return None

        # Attributions are captured even when the licence is unusable: a
        # package whose declared licence is NOASSERTION can still have known
        # copyright holders, and the NOTICE wants them either way.
        self.last_attributions = extract_attributions(payload)

        spdx_id = extract_declared_license(payload)
        if spdx_id is None:
            log.info("clearlydefined_license_unmapped", purl=purl)
            return None

        coordinates = url.split("/definitions/", 1)[1]
        return LicenseFetchResult(
            spdx_id=spdx_id,
            reference_url=f"https://clearlydefined.io/definitions/{coordinates}",
            source=self.source,
        )


__all__ = [
    "ClearlyDefinedLicenseFetcher",
    "extract_attributions",
    "extract_declared_license",
    "parse_purl",
]
