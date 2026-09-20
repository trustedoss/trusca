# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Multi-ecosystem license fetcher dispatch + cache layer.

Public surface
--------------
* :func:`fetch_license` — given a versioned PURL, return a
  :class:`LicenseFetchResult` (or ``None`` if unknown). Consults the
  ``license_fetch_cache`` table first; on cache miss / TTL expiry it
  routes to the per-ecosystem adapter, then writes the result back
  (positive or negative).
* :func:`cache_ttl_seconds` — runtime accessor for the TTL window
  (24h default). Reads ``LICENSE_FETCH_TTL_SECONDS`` at call time per
  CLAUDE.md core rule #11; tests can override via monkeypatch.
* :data:`PURL_PREFIX_TO_FETCHER` — purl-prefix → fetcher class map,
  re-exported for tests that want to plug a stub fetcher.

Threading / concurrency
-----------------------
The dispatcher is callable from multiple Celery worker threads. Each
call opens (and closes) a short-lived ``Session`` from the sync
session factory; the per-host throttle inside :mod:`base` serialises
parallel HTTP requests to the same registry.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .base import LicenseFetchResult
from .budget import EnrichmentBudget, active_budget
from .clearlydefined import ClearlyDefinedLicenseFetcher
from .crates import CratesLicenseFetcher
from .maven import MavenLicenseFetcher
from .nuget import NuGetLicenseFetcher
from .pkggo import PkgGoLicenseFetcher
from .pypi import PyPILicenseFetcher
from .rubygems import RubyGemsLicenseFetcher

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .base import LicenseFetcher

log = structlog.get_logger("integrations.license_fetcher")


# ---------------------------------------------------------------------------
# TTL knob
# ---------------------------------------------------------------------------

_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


#: Attributions from the most recent ClearlyDefined lookup, keyed by purl.
#:
#: A side channel rather than a field on :class:`LicenseFetchResult`, because
#: that dataclass is the contract all seven fetchers share and only one of them
#: can ever populate this. The caller reads it immediately after
#: :func:`fetch_license` and clears it; nothing accumulates across a scan.
LAST_ATTRIBUTIONS: dict[str, list[str]] = {}


def take_attributions(purl: str) -> list[str]:
    """Pop the attributions the last lookup of *purl* produced."""
    return LAST_ATTRIBUTIONS.pop(purl, [])


def clearlydefined_enabled() -> bool:
    """Whether the ClearlyDefined fallback may run. Read at call time (rule #11).

    Default OFF, fail-closed. `api.clearlydefined.io` is a host this deployment
    has never talked to, and the convention here is that a NEW egress target
    opts in explicitly while an existing one may default on. An operator turns
    it on with ``CLEARLYDEFINED_ENABLED=true``; air-gapped installs leave it
    alone and lose nothing they had.
    """
    return os.getenv("CLEARLYDEFINED_ENABLED", "").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }


def cache_ttl_seconds() -> int:
    """Resolve the cache TTL at call time (CLAUDE.md core rule #11).

    Override by setting ``LICENSE_FETCH_TTL_SECONDS`` in the worker
    environment; tests pass their own value via monkeypatch. Anything
    non-integer falls back to the 24h default with a debug log.
    """
    raw = os.getenv("LICENSE_FETCH_TTL_SECONDS")
    if raw is None:
        return _DEFAULT_TTL_SECONDS
    try:
        value = int(raw)
    except ValueError:
        log.warning("license_fetch_ttl_invalid", raw=raw)
        return _DEFAULT_TTL_SECONDS
    if value <= 0:
        return _DEFAULT_TTL_SECONDS
    return value


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

# A factory map (rather than instances) so each dispatch call can hand
# the per-ecosystem adapter its own httpx.Client — avoids leaking a
# half-closed client across worker threads. Tests substitute a stub
# factory via monkeypatch on this dict.
PURL_PREFIX_TO_FETCHER: dict[str, Callable[[], LicenseFetcher]] = {
    "pkg:maven/": MavenLicenseFetcher,
    "pkg:pypi/": PyPILicenseFetcher,
    "pkg:cargo/": CratesLicenseFetcher,
    "pkg:golang/": PkgGoLicenseFetcher,
    "pkg:gem/": RubyGemsLicenseFetcher,
    "pkg:nuget/": NuGetLicenseFetcher,
}


def _fetcher_for(purl: str) -> LicenseFetcher | None:
    for prefix, factory in PURL_PREFIX_TO_FETCHER.items():
        if purl.startswith(prefix):
            return factory()
    return None


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _row_to_result(
    *,
    spdx_id: str | None,
    reference_url: str | None,
    source: str,
    is_negative: bool,
) -> LicenseFetchResult | None:
    """Materialise a cache row back into a public ``LicenseFetchResult``."""
    if is_negative or spdx_id is None:
        return None
    # A licence taken from a parent POM carries the ancestor it came from in
    # the ``source`` column ("maven_central_parent|group:artifact:version"), so
    # a cache hit reports the same provenance as the lookup that filled it.
    base_source, _, inherited_from = source.partition("|")
    return LicenseFetchResult(
        spdx_id=spdx_id,
        reference_url=reference_url,
        source=base_source,
        inherited_from=inherited_from or None,
    )


def _cache_lookup(
    session: Session,
    *,
    purl: str,
    now: datetime,
    ttl_seconds: int,
) -> tuple[bool, LicenseFetchResult | None]:
    """Return ``(hit, result)``.

    ``hit=True`` means the cache served the answer (positive or
    negative). ``hit=False`` means caller should run the fetcher.
    """
    from models import LicenseFetchCache

    row = session.execute(
        select(LicenseFetchCache).where(LicenseFetchCache.purl == purl)
    ).scalar_one_or_none()
    if row is None:
        return False, None
    age = now - row.fetched_at
    if age > timedelta(seconds=ttl_seconds):
        return False, None
    return True, _row_to_result(
        spdx_id=row.spdx_id,
        reference_url=row.reference_url,
        source=row.source,
        is_negative=row.is_negative,
    )


def _cache_write(
    session: Session,
    *,
    purl: str,
    result: LicenseFetchResult | None,
    fallback_source: str,
    now: datetime,
) -> None:
    """UPSERT a cache row keyed on ``purl``.

    A positive answer stores ``spdx_id`` + ``reference_url``;
    a negative answer (``result is None``) records
    ``is_negative=True`` and a NULL ``spdx_id`` so the next lookup
    in the TTL window short-circuits to ``None`` without an HTTP
    call.
    """
    from models import LicenseFetchCache

    if result is None:
        values: dict[str, object] = {
            "purl": purl,
            "spdx_id": None,
            "reference_url": None,
            "source": fallback_source,
            "is_negative": True,
            "fetched_at": now,
        }
    else:
        values = {
            "purl": purl,
            "spdx_id": result.spdx_id,
            "reference_url": result.reference_url,
            "source": (
                f"{result.source}|{result.inherited_from}"
                if result.inherited_from
                else result.source
            ),
            "is_negative": False,
            "fetched_at": now,
        }
    stmt = pg_insert(LicenseFetchCache).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["purl"],
        set_={
            "spdx_id": stmt.excluded.spdx_id,
            "reference_url": stmt.excluded.reference_url,
            "source": stmt.excluded.source,
            "is_negative": stmt.excluded.is_negative,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    session.execute(stmt)


class _SessionAncestorCache:
    """The dispatcher's cache, lent to a fetcher that follows a parent chain.

    Parent POMs are shared between children (a dozen artifacts of one project
    all name the same parent), so the answer for a parent is stored under the
    parent's own purl and every later child reads it back for free.
    """

    def __init__(self, session: Session, *, now: datetime, ttl_seconds: int) -> None:
        self._session = session
        self._now = now
        self._ttl_seconds = ttl_seconds

    def lookup(self, purl: str) -> tuple[bool, LicenseFetchResult | None]:
        return _cache_lookup(
            self._session, purl=purl, now=self._now, ttl_seconds=self._ttl_seconds
        )

    def store(self, purl: str, result: LicenseFetchResult) -> None:
        _cache_write(
            self._session,
            purl=purl,
            result=result,
            fallback_source=result.source,
            now=self._now,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_license(
    purl: str,
    *,
    session: Session,
    now: datetime | None = None,
    ttl_seconds: int | None = None,
) -> LicenseFetchResult | None:
    """Resolve a license for a versioned PURL with caching.

    The cache flow:
        1. Look up ``purl`` in ``license_fetch_cache``.
        2. If a fresh row exists, return its answer (positive or
           negative) without any HTTP traffic.
        3. Otherwise dispatch to the ecosystem-specific fetcher.
        4. UPSERT the answer back into the cache (positive answers
           and confirmed misses share a TTL).

    The session is *not* committed here — the caller (typically
    ``_persist_components``) commits along with the rest of its scan
    persistence work so a failure mid-scan does not leave behind
    half-written cache rows.
    """
    if not purl:
        return None
    effective_now = now or datetime.now(UTC)
    effective_ttl = ttl_seconds if ttl_seconds is not None else cache_ttl_seconds()

    hit, result = _cache_lookup(
        session, purl=purl, now=effective_now, ttl_seconds=effective_ttl
    )
    if hit:
        log.debug("license_fetch_cache_hit", purl=purl, negative=result is None)
        return result

    fetcher = _fetcher_for(purl)
    # A purl no adapter claims, with ClearlyDefined off, is answered without
    # touching the network, so the budget has nothing to say about it.
    budget = active_budget() if (fetcher is not None or clearlydefined_enabled()) else None
    if budget is not None:
        # Cache hits above were served for free. Only a lookup that would go
        # out on the network is refused, and it is refused BEFORE anything is
        # written: a skipped lookup must not be cached as a confirmed miss.
        reason = budget.refusal()
        if reason is not None:
            budget.note_skipped(reason)
            return None

    started = time.monotonic()
    failures_before = budget.transport_failures if budget is not None else 0
    try:
        return _dispatch_and_cache(
            purl,
            session=session,
            fetcher=fetcher,
            now=effective_now,
            budget=budget,
            failures_before=failures_before,
            ttl_seconds=effective_ttl,
        )
    finally:
        if budget is not None:
            budget.add_elapsed(time.monotonic() - started)


def _dispatch_and_cache(
    purl: str,
    *,
    session: Session,
    fetcher: LicenseFetcher | None,
    now: datetime,
    budget: EnrichmentBudget | None,
    failures_before: int,
    ttl_seconds: int,
) -> LicenseFetchResult | None:
    new_result: LicenseFetchResult | None = None
    fallback_source = "unsupported_ecosystem"

    if fetcher is not None:
        if isinstance(fetcher, MavenLicenseFetcher):
            fetcher.ancestor_cache = _SessionAncestorCache(
                session, now=now, ttl_seconds=ttl_seconds
            )
        try:
            new_result = fetcher.fetch(purl)
        finally:
            # Adapters that own their httpx.Client must release it.
            close = getattr(fetcher, "close", None)
            if callable(close):
                close()
        fallback_source = getattr(fetcher, "source", "unknown")

    # S5-A — ClearlyDefined is the fallback, not a seventh registry adapter. It
    # runs when no adapter claimed the ecosystem (npm has never had one, so
    # every npm component went straight to a negative entry without a single
    # request) or when the one that did came back empty. Asking it first would
    # put a hop in front of six sources that answer authoritatively; asking it
    # last turns "not found" into "not found anywhere we know to look".
    if new_result is None and clearlydefined_enabled():
        cd_fetcher = ClearlyDefinedLicenseFetcher()
        try:
            new_result = cd_fetcher.fetch(purl)
        finally:
            cd_fetcher.close()
        # Attributions are worth keeping even when the licence is not. A
        # compound declaration ("CC0-1.0 AND MIT") normalises to nothing here,
        # because a LicenseFetchResult holds one id — but the copyright holders
        # it came with are exactly what the NOTICE is short of.
        LAST_ATTRIBUTIONS[purl] = list(cd_fetcher.last_attributions)
        if new_result is not None:
            fallback_source = cd_fetcher.source

    if new_result is None and (
        (budget is not None and budget.transport_failures > failures_before)
        # A parent lookup the budget refused is the same: the chain was cut
        # short, not found empty.
        or getattr(fetcher, "lookup_incomplete", False)
    ):
        # The registry never answered, so this is not a confirmed miss. Caching
        # it would report "looked up, nothing there" for the next 24 hours.
        return None

    _cache_write(
        session,
        purl=purl,
        result=new_result,
        fallback_source=fallback_source,
        now=now,
    )
    return new_result


__all__ = [
    "PURL_PREFIX_TO_FETCHER",
    "clearlydefined_enabled",
    "take_attributions",
    "LicenseFetchResult",
    "cache_ttl_seconds",
    "fetch_license",
]
