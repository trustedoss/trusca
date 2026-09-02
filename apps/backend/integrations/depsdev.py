# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
deps.dev client: exact-match package lookup and advisory lookup.

deps.dev (https://api.deps.dev/v3) has no fuzzy/full-text search endpoint --
only exact lookups by (system, name) or by advisory id. Two operations:

* :func:`lookup_package` -- versions list, then the default (or most
  recently published) version's detail (licenses, advisories, links).
* :func:`lookup_advisory` -- advisory metadata (title, CVSS, aliases) by
  CVE or GHSA id; both id shapes are accepted by the same endpoint.

Called inline from a FastAPI request (the caller is waiting on a button
click or a debounced search keystroke), not from a Celery worker, so this
mirrors the pattern in ``integrations.oauth`` (``httpx.AsyncClient`` awaited
inline) rather than ``integrations.license_fetcher`` (sync, worker-only).
The response-handling hardening is borrowed from ``license_fetcher.base``
regardless -- deps.dev is an untrusted external response like any registry:
streamed with a body-size cap (a legitimate response here is tens of KiB),
``follow_redirects=False``, and a broad ``httpx.HTTPError`` /
``httpx.InvalidURL`` catch so a network-layer surprise (a misconfigured
proxy, a DNS failure) degrades to a clean upstream error instead of a 500
traceback.

Not found is a normal outcome, not an error: a 404 from deps.dev means "no
such package/advisory" and is returned as ``found=False``, same as any other
lookup miss. Both public functions instead raise ``ValueError`` for bad
input (caller maps to 422) and can let ``DepsDevUpstreamError`` /
``httpx.HTTPError`` / ``httpx.InvalidURL`` propagate for anything deps.dev
did not answer cleanly (caller maps to 502)::

    try:
        result = await lookup_package(ecosystem_slug, name)
    except ValueError:
        ...  # 422
    except (DepsDevUpstreamError, httpx.HTTPError, httpx.InvalidURL):
        ...  # 502
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
import structlog

log = structlog.get_logger("integrations.depsdev")

BASE_URL = "https://api.deps.dev/v3"

#: A single deps.dev call is inline on a request the caller is waiting on
#: (a button click, or a debounced search keystroke) -- this is an
#: eventloop-blocking await, not a background task, so the ceiling on one
#: stall is kept short (mirrors ``oauth.oidc.DISCOVERY_TIMEOUT_SECONDS``).
#: A package lookup makes two calls sequentially, so its worst case is
#: twice this.
TIMEOUT_SECONDS = 3.0

#: A legitimate deps.dev response (a version list or a version/advisory
#: detail document) is tens of KiB. 512 KiB leaves generous headroom without
#: trusting an unbounded read from a third party.
MAX_BODY_BYTES = 512 * 1024

#: Per-process cap on concurrent deps.dev calls. Multiple uvicorn workers
#: each hold their own semaphore, so the real ceiling is this number times
#: the worker count -- a known, accepted limitation (the same one
#: ``license_fetcher`` documents for its own per-host gate), not something
#: this module tracks across processes.
_MAX_CONCURRENT = 4

SYSTEM_SLUGS = frozenset({"npm", "pypi", "maven", "go", "cargo", "nuget"})

#: purl type per deps.dev system slug, for ``services.purl_build.build_purl``.
#: Only "go" differs from its own slug -- the purl type is "golang".
PURL_TYPE_BY_SLUG: dict[str, str] = {
    "npm": "npm",
    "pypi": "pypi",
    "maven": "maven",
    "go": "golang",
    "cargo": "cargo",
    "nuget": "nuget",
}

_MAX_NAME_LEN = 255
_MAX_ADVISORY_ID_LEN = 64
_ADVISORY_IDS_CAP = 20


class DepsDevUpstreamError(Exception):
    """deps.dev answered, but not usefully: a non-2xx/404 status, a
    redirect, or a 200 whose body was not JSON."""


@dataclass(frozen=True)
class ExternalPackageLookup:
    ecosystem: str
    name: str
    found: bool
    version: str | None = None
    purl: str | None = None
    licenses: list[str] = field(default_factory=list)
    advisory_count: int = 0
    advisory_ids: list[str] = field(default_factory=list)
    homepage_url: str | None = None
    source_repo_url: str | None = None


@dataclass(frozen=True)
class ExternalAdvisoryLookup:
    advisory_id: str
    found: bool
    title: str | None = None
    cvss3_score: float | None = None
    cvss3_vector: str | None = None
    aliases: list[str] = field(default_factory=list)


def _has_control_char(value: str) -> bool:
    return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)


_semaphore: asyncio.Semaphore | None = None


def _concurrency_gate() -> asyncio.Semaphore:
    # Created lazily, on the running event loop, rather than at import time.
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    return _semaphore


class _NullContext(AbstractAsyncContextManager[httpx.AsyncClient]):
    """Hands back an already-open client without closing it on exit.

    Used when the caller supplied ``http_client`` (tests, mainly) -- that
    client's lifecycle belongs to the caller, not to this call.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._client

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _client_context(
    http_client: httpx.AsyncClient | None,
) -> AbstractAsyncContextManager[httpx.AsyncClient]:
    if http_client is not None:
        return _NullContext(http_client)
    return _make_client()


async def _fetch_json(client: httpx.AsyncClient, url: str) -> dict[str, Any] | None:
    """GET ``url``, streamed with a body-size cap; ``None`` on a 404.

    Raises :class:`DepsDevUpstreamError` for anything else deps.dev did not
    hand back cleanly (non-2xx/404 status, a redirect, or a non-JSON body).
    """
    async with client.stream("GET", url) as response:
        status = response.status_code
        if status == 404:
            # deps.dev returns 404 for both an unknown package and an
            # unknown system slug -- the caller validates the slug against
            # SYSTEM_SLUGS separately, so a 404 here always means "no such
            # package/advisory".
            return None
        if not (200 <= status < 300):
            log.warning("depsdev_unexpected_status", url=url[:200], status=status)
            raise DepsDevUpstreamError(f"deps.dev returned HTTP {status}")

        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            # Two counters, same reasoning as license_fetcher.base: a
            # single network read can expand far past its wire size inside
            # decompression before this loop sees it, so the wire byte
            # count (num_bytes_downloaded) is capped alongside the
            # decompressed one.
            if size > MAX_BODY_BYTES or response.num_bytes_downloaded > MAX_BODY_BYTES:
                log.warning("depsdev_body_too_large", url=url[:200], limit_bytes=MAX_BODY_BYTES)
                raise DepsDevUpstreamError("deps.dev response exceeded the size cap")
            chunks.append(chunk)

    body = b"".join(chunks)
    try:
        parsed = httpx.Response(status_code=status, content=body).json()
    except ValueError as exc:
        log.warning("depsdev_non_json_body", url=url[:200])
        raise DepsDevUpstreamError("deps.dev returned a non-JSON body") from exc
    if not isinstance(parsed, dict):
        raise DepsDevUpstreamError("deps.dev returned a non-object body")
    return parsed


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
    )


def _pick_version(versions: object) -> str | None:
    """The default version, or the most recently published one, or ``None``.

    deps.dev marks exactly one version ``isDefault: true`` in every package
    observed so far, but the field is not documented as guaranteed, so both
    "no default marked" and "empty versions list" are handled rather than
    indexed into blindly.
    """
    if not isinstance(versions, list) or not versions:
        return None
    for entry in versions:
        if isinstance(entry, dict) and entry.get("isDefault") is True:
            version = _extract_version(entry.get("versionKey"))
            if version is not None:
                return version

    def _published_at(entry: object) -> str:
        if isinstance(entry, dict):
            published = entry.get("publishedAt")
            if isinstance(published, str):
                return published
        return ""

    latest = max(versions, key=_published_at, default=None)
    if isinstance(latest, dict):
        return _extract_version(latest.get("versionKey"))
    return None


def _extract_version(version_key: object) -> str | None:
    if isinstance(version_key, dict):
        version = version_key.get("version")
        if isinstance(version, str):
            return version
    return None


async def lookup_package(
    ecosystem_slug: str, name: str, *, http_client: httpx.AsyncClient | None = None
) -> ExternalPackageLookup:
    """Look up a package by (ecosystem slug, exact name).

    Raises ``ValueError`` for an unknown ecosystem slug or an invalid name
    (empty, too long, or carrying a control character). Otherwise always
    returns an :class:`ExternalPackageLookup` -- ``found=False`` when
    deps.dev has no such package, so "not found" and "invalid input" are
    distinguishable to the caller.
    """
    if ecosystem_slug not in SYSTEM_SLUGS:
        raise ValueError(f"unknown ecosystem: {ecosystem_slug!r}")
    if not name or len(name) > _MAX_NAME_LEN or _has_control_char(name):
        raise ValueError("invalid package name")

    encoded_name = quote(name, safe="")
    async with _concurrency_gate(), _client_context(http_client) as client:
        versions_doc = await _fetch_json(
            client, f"/systems/{ecosystem_slug}/packages/{encoded_name}"
        )
        if versions_doc is None:
            return ExternalPackageLookup(ecosystem=ecosystem_slug, name=name, found=False)

        version = _pick_version(versions_doc.get("versions"))
        if version is None:
            return ExternalPackageLookup(ecosystem=ecosystem_slug, name=name, found=False)

        detail = await _fetch_json(
            client,
            f"/systems/{ecosystem_slug}/packages/{encoded_name}"
            f"/versions/{quote(version, safe='')}",
        )
        if detail is None:
            log.warning(
                "depsdev_version_detail_missing",
                ecosystem=ecosystem_slug,
                name=name[:200],
                version=version,
            )
            return ExternalPackageLookup(ecosystem=ecosystem_slug, name=name, found=False)

    # Local import: avoids a module-level cycle if services ever needs to
    # import from integrations (it does not today, but integrations does
    # not otherwise import from services).
    from services.purl_build import build_purl

    purl_type = PURL_TYPE_BY_SLUG[ecosystem_slug]
    purl = build_purl(purl_type, name, None)

    licenses = [item for item in (detail.get("licenses") or []) if isinstance(item, str)]
    advisory_ids = [
        entry["id"]
        for entry in (detail.get("advisoryKeys") or [])
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    ]
    links = {
        item.get("label"): item.get("url")
        for item in (detail.get("links") or [])
        if isinstance(item, dict) and isinstance(item.get("label"), str)
    }

    return ExternalPackageLookup(
        ecosystem=ecosystem_slug,
        name=name,
        found=True,
        version=version,
        purl=purl,
        licenses=licenses,
        advisory_count=len(advisory_ids),
        advisory_ids=advisory_ids[:_ADVISORY_IDS_CAP],
        homepage_url=links.get("HOMEPAGE"),
        source_repo_url=links.get("SOURCE_REPO"),
    )


async def lookup_advisory(
    advisory_id: str, *, http_client: httpx.AsyncClient | None = None
) -> ExternalAdvisoryLookup:
    """Look up advisory metadata by CVE or GHSA id (deps.dev accepts both
    on the same endpoint). Raises ``ValueError`` for an invalid id."""
    if (
        not advisory_id
        or len(advisory_id) > _MAX_ADVISORY_ID_LEN
        or _has_control_char(advisory_id)
    ):
        raise ValueError("invalid advisory id")

    async with _concurrency_gate(), _client_context(http_client) as client:
        doc = await _fetch_json(client, f"/advisories/{quote(advisory_id, safe='')}")

    if doc is None:
        return ExternalAdvisoryLookup(advisory_id=advisory_id, found=False)

    title = doc.get("title")
    score = doc.get("cvss3Score")
    vector = doc.get("cvss3Vector")
    aliases = [item for item in (doc.get("aliases") or []) if isinstance(item, str)]

    return ExternalAdvisoryLookup(
        advisory_id=advisory_id,
        found=True,
        title=title if isinstance(title, str) else None,
        cvss3_score=score if isinstance(score, int | float) else None,
        cvss3_vector=vector if isinstance(vector, str) else None,
        aliases=aliases,
    )
