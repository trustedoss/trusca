"""
Unit tests for the dispatcher in ``integrations.license_fetcher``.

The dispatcher decides which fetcher class handles a PURL prefix, reads
the cache, calls the fetcher, and writes the result back. We exercise
all branches with a stubbed fetcher (no real HTTP) and a stubbed
cache lookup/write pair so the test does not need a live database —
the cache table itself is exercised in
``tests/integration/scan/test_license_fetcher_integration.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.orm import Session

import integrations.license_fetcher as dispatcher_mod
from integrations.license_fetcher import (
    PURL_PREFIX_TO_FETCHER,
    cache_ttl_seconds,
    fetch_license,
)
from integrations.license_fetcher.base import LicenseFetchResult


def _fake_session() -> Session:
    """The dispatcher's ``Session`` parameter is fully stubbed in this suite —
    we override ``_cache_lookup`` / ``_cache_write`` to skip the SQL layer.
    Returning a plain object cast to ``Session`` is type-safe for the unit
    suite while keeping the public ``fetch_license`` signature honest in
    production code."""
    return cast(Session, object())


class _StubFetcher:
    """Minimal LicenseFetcher stub.

    Returns ``result`` for every call and counts how many times it
    was invoked so cache-hit / cache-miss assertions can pin call
    counts.
    """

    source = "stub"

    def __init__(self, result: LicenseFetchResult | None) -> None:
        self.result = result
        self.calls = 0
        self.closed = False

    def fetch(
        self, purl: str, *, timeout: float = 30.0  # noqa: ARG002
    ) -> LicenseFetchResult | None:
        self.calls += 1
        return self.result

    def close(self) -> None:
        self.closed = True


class _CacheStub:
    """In-memory replacement for ``_cache_lookup`` / ``_cache_write``.

    Keys are PURLs; values are tuples that mirror the cache row
    layout (``spdx_id``, ``reference_url``, ``source``,
    ``is_negative``, ``fetched_at``). Tests prime entries directly
    by writing to ``rows``.
    """

    def __init__(self) -> None:
        self.rows: dict[
            str, tuple[str | None, str | None, str, bool, datetime]
        ] = {}

    def lookup(
        self, session: object, *, purl: str, now: datetime, ttl_seconds: int
    ) -> tuple[bool, LicenseFetchResult | None]:
        existing = self.rows.get(purl)
        if existing is None:
            return False, None
        spdx_id, ref_url, source, negative, fetched_at = existing
        if now - fetched_at > timedelta(seconds=ttl_seconds):
            return False, None
        if negative or spdx_id is None:
            return True, None
        return True, LicenseFetchResult(
            spdx_id=spdx_id, reference_url=ref_url, source=source
        )

    def write(
        self,
        session: object,
        *,
        purl: str,
        result: LicenseFetchResult | None,
        fallback_source: str,
        now: datetime,
    ) -> None:
        if result is None:
            self.rows[purl] = (None, None, fallback_source, True, now)
        else:
            self.rows[purl] = (
                result.spdx_id,
                result.reference_url,
                result.source,
                False,
                now,
            )


@pytest.fixture
def cache_stub(monkeypatch: pytest.MonkeyPatch) -> _CacheStub:
    """Replace the real cache helpers with the in-memory stub."""
    stub = _CacheStub()
    monkeypatch.setattr(dispatcher_mod, "_cache_lookup", stub.lookup)
    monkeypatch.setattr(dispatcher_mod, "_cache_write", stub.write)
    return stub


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------


def test_dispatch_routes_maven_pypi_crates_pkggo_by_prefix() -> None:
    assert "pkg:maven/" in PURL_PREFIX_TO_FETCHER
    assert "pkg:pypi/" in PURL_PREFIX_TO_FETCHER
    assert "pkg:cargo/" in PURL_PREFIX_TO_FETCHER
    assert "pkg:golang/" in PURL_PREFIX_TO_FETCHER
    assert len(PURL_PREFIX_TO_FETCHER) == 4


# ---------------------------------------------------------------------------
# fetch_license — dispatch & cache flows
# ---------------------------------------------------------------------------


def test_fetch_license_calls_fetcher_on_cache_miss(
    monkeypatch: pytest.MonkeyPatch, cache_stub: _CacheStub
) -> None:
    stub = _StubFetcher(
        LicenseFetchResult(
            spdx_id="Apache-2.0",
            reference_url="https://example.invalid/LICENSE",
            source="maven_central",
        )
    )
    monkeypatch.setitem(PURL_PREFIX_TO_FETCHER, "pkg:maven/", lambda: stub)

    result = fetch_license("pkg:maven/foo/bar@1.0", session=_fake_session())

    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert stub.calls == 1
    assert stub.closed is True
    assert "pkg:maven/foo/bar@1.0" in cache_stub.rows
    cached = cache_stub.rows["pkg:maven/foo/bar@1.0"]
    assert cached[0] == "Apache-2.0"
    assert cached[3] is False  # is_negative


def test_fetch_license_returns_cached_positive_without_calling_fetcher(
    monkeypatch: pytest.MonkeyPatch, cache_stub: _CacheStub
) -> None:
    stub = _StubFetcher(None)  # would return None if called — guards against accidents
    monkeypatch.setitem(PURL_PREFIX_TO_FETCHER, "pkg:maven/", lambda: stub)

    cache_stub.rows["pkg:maven/foo/bar@1.0"] = (
        "MIT",
        None,
        "maven_central",
        False,
        datetime.now(UTC),
    )

    result = fetch_license("pkg:maven/foo/bar@1.0", session=_fake_session())
    assert result is not None
    assert result.spdx_id == "MIT"
    assert stub.calls == 0


def test_fetch_license_returns_cached_negative_without_calling_fetcher(
    monkeypatch: pytest.MonkeyPatch, cache_stub: _CacheStub
) -> None:
    stub = _StubFetcher(
        LicenseFetchResult(spdx_id="Apache-2.0", reference_url=None, source="maven_central")
    )
    monkeypatch.setitem(PURL_PREFIX_TO_FETCHER, "pkg:maven/", lambda: stub)

    cache_stub.rows["pkg:maven/foo/bar@1.0"] = (
        None,
        None,
        "maven_central",
        True,
        datetime.now(UTC),
    )

    result = fetch_license("pkg:maven/foo/bar@1.0", session=_fake_session())
    assert result is None
    assert stub.calls == 0


def test_fetch_license_refetches_on_expired_cache(
    monkeypatch: pytest.MonkeyPatch, cache_stub: _CacheStub
) -> None:
    stub = _StubFetcher(
        LicenseFetchResult(
            spdx_id="Apache-2.0", reference_url=None, source="maven_central"
        )
    )
    monkeypatch.setitem(PURL_PREFIX_TO_FETCHER, "pkg:maven/", lambda: stub)

    stale = datetime.now(UTC) - timedelta(days=2)
    cache_stub.rows["pkg:maven/foo/bar@1.0"] = (
        "MIT",
        None,
        "maven_central",
        False,
        stale,
    )

    result = fetch_license("pkg:maven/foo/bar@1.0", session=_fake_session())
    assert result is not None
    assert result.spdx_id == "Apache-2.0"
    assert stub.calls == 1


def test_fetch_license_returns_none_for_unsupported_ecosystem(
    cache_stub: _CacheStub,
) -> None:
    result = fetch_license("pkg:nuget/Foo@1.0.0", session=_fake_session())
    assert result is None
    assert "pkg:nuget/Foo@1.0.0" in cache_stub.rows
    assert cache_stub.rows["pkg:nuget/Foo@1.0.0"][3] is True  # negative


def test_fetch_license_writes_negative_cache_on_fetcher_none(
    monkeypatch: pytest.MonkeyPatch, cache_stub: _CacheStub
) -> None:
    stub = _StubFetcher(None)
    monkeypatch.setitem(PURL_PREFIX_TO_FETCHER, "pkg:maven/", lambda: stub)

    result = fetch_license("pkg:maven/foo/bar@1.0", session=_fake_session())
    assert result is None
    cached = cache_stub.rows["pkg:maven/foo/bar@1.0"]
    assert cached[3] is True  # is_negative
    assert cached[0] is None  # spdx_id


def test_fetch_license_returns_none_on_empty_purl(cache_stub: _CacheStub) -> None:
    assert fetch_license("", session=_fake_session()) is None
    assert cache_stub.rows == {}


# ---------------------------------------------------------------------------
# cache_ttl_seconds
# ---------------------------------------------------------------------------


def test_cache_ttl_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LICENSE_FETCH_TTL_SECONDS", raising=False)
    assert cache_ttl_seconds() == 24 * 60 * 60


def test_cache_ttl_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LICENSE_FETCH_TTL_SECONDS", "3600")
    assert cache_ttl_seconds() == 3600


@pytest.mark.parametrize("invalid", ["abc", "0", "-5", ""])
def test_cache_ttl_falls_back_on_invalid(
    monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_TTL_SECONDS", invalid)
    assert cache_ttl_seconds() == 24 * 60 * 60
