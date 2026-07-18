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
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .base import LicenseFetchResult
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
    return LicenseFetchResult(
        spdx_id=spdx_id,
        reference_url=reference_url,
        source=source,
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
            "source": result.source,
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
    if fetcher is None:
        # No registry adapter for this ecosystem — record a negative
        # cache hit so we don't re-evaluate the prefix on every call.
        _cache_write(
            session,
            purl=purl,
            result=None,
            fallback_source="unsupported_ecosystem",
            now=effective_now,
        )
        return None

    try:
        new_result = fetcher.fetch(purl)
    finally:
        # Adapters that own their httpx.Client must release it.
        close = getattr(fetcher, "close", None)
        if callable(close):
            close()

    fallback_source = getattr(fetcher, "source", "unknown")
    _cache_write(
        session,
        purl=purl,
        result=new_result,
        fallback_source=fallback_source,
        now=effective_now,
    )
    return new_result


__all__ = [
    "PURL_PREFIX_TO_FETCHER",
    "LicenseFetchResult",
    "cache_ttl_seconds",
    "fetch_license",
]
