"""U1-A, the per-scan budget and circuit breaker on licence enrichment.

Failure injection against the real retry loop: a RubyGems adapter over an
``httpx.MockTransport`` that times out, answers 404, or answers slowly, driven
through ``persist_sbom_components`` against a real Postgres. What this pins is
the distinction the feature exists for. A lookup that was *skipped* (budget
spent, breaker open) or *never answered* (transport failure) is not the same
as one that *came back empty*, so it must leave no cache row and must be
recorded on the scan.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import integrations.license_fetcher as dispatcher_mod
import integrations.license_fetcher.base as base_mod
import integrations.license_fetcher.rubygems as rubygems_mod
from models import LicenseFetchCache, LicenseFinding, Scan
from tests._db_required import migrate_to_head
from tests.integration.scan.test_persist_sbom_bulk_catalog import _seed_queued_scan

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


class _Clock:
    """Stands in for ``time`` inside the dispatcher so elapsed time is exact."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


def _gem_sbom(count: int) -> tuple[dict[str, Any], list[str]]:
    tag = uuid.uuid4().hex[:10]
    purls = [f"pkg:gem/u1a-{tag}-{i}@1.0.0" for i in range(count)]
    components = [
        {
            "type": "library",
            "name": f"u1a-{tag}-{i}",
            "version": "1.0.0",
            "purl": purl,
            "bom-ref": purl,
        }
        for i, purl in enumerate(purls)
    ]
    return {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": components}, purls


def _registry(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Route ``pkg:gem/`` to a real RubyGems adapter talking to ``handler``."""
    client = httpx.Client(transport=httpx.MockTransport(handler), timeout=1.0)
    monkeypatch.setitem(
        dispatcher_mod.PURL_PREFIX_TO_FETCHER,
        "pkg:gem/",
        lambda: rubygems_mod.RubyGemsLicenseFetcher(http=client),
    )
    # The retry loop would really sleep between attempts. Same loop, no waiting.
    monkeypatch.setattr(
        rubygems_mod,
        "request_with_retry",
        functools.partial(base_mod.request_with_retry, sleep=lambda _s: None),
    )
    base_mod._HOST_LOCKS.clear()


def _run(sync_session: Session, sbom: dict[str, Any]) -> uuid.UUID:
    from tasks.scan_source import persist_sbom_components

    scan_id = _seed_queued_scan()
    persist_sbom_components(sync_session, scan_uuid=scan_id, sbom=sbom)
    sync_session.commit()
    return scan_id


def _enrichment(sync_session: Session, scan_id: uuid.UUID) -> dict[str, Any] | None:
    sync_session.expire_all()
    scan = sync_session.get(Scan, scan_id)
    assert scan is not None
    return (scan.scan_metadata or {}).get("license_enrichment")


def _cached(sync_session: Session, purls: list[str]) -> dict[str, LicenseFetchCache]:
    rows = sync_session.execute(
        select(LicenseFetchCache).where(LicenseFetchCache.purl.in_(purls))
    ).scalars()
    return {row.purl: row for row in rows}


def _ok(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
    return httpx.Response(200, json={"licenses": ["MIT"]})


def test_dead_registry_trips_breaker_and_is_not_cached_as_a_miss(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "true")
    monkeypatch.setenv("LICENSE_FETCH_CONSECUTIVE_FAILURE_LIMIT", "5")
    requests: list[str] = []

    def dead(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        raise httpx.ConnectTimeout("no answer", request=request)

    _registry(monkeypatch, dead)
    sbom, purls = _gem_sbom(40)

    scan_id = _run(sync_session, sbom)

    # 5 lookups x (1 try + 3 retries). Without the breaker it is 40 x 4.
    assert len(requests) == 5 * 4
    # Nothing is cached: not the 5 that timed out, not the 35 never tried.
    assert _cached(sync_session, purls) == {}
    record = _enrichment(sync_session, scan_id)
    assert record is not None
    assert record["not_looked_up"] == 35
    assert record["not_looked_up_reason"] == "breaker_open"
    assert record["lookup_failures"] == 5


def test_registry_answering_not_found_is_not_a_failure(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "true")
    monkeypatch.setenv("LICENSE_FETCH_CONSECUTIVE_FAILURE_LIMIT", "3")
    _registry(monkeypatch, lambda request: httpx.Response(404))
    sbom, purls = _gem_sbom(12)

    scan_id = _run(sync_session, sbom)

    cached = _cached(sync_session, purls)
    # Every lookup ran, and each confirmed miss is a real negative entry.
    assert len(cached) == 12
    assert all(row.is_negative for row in cached.values())
    assert _enrichment(sync_session, scan_id) is None


def test_time_budget_stops_lookups_and_skipped_ones_are_not_cached(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "true")
    monkeypatch.setenv("LICENSE_FETCH_SCAN_BUDGET_SECONDS", "25")
    clock = _Clock()
    monkeypatch.setattr(dispatcher_mod, "time", clock)
    requests = 0

    def slow(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        nonlocal requests
        requests += 1
        clock.now += 10.0  # each registry call "takes" 10 s
        return httpx.Response(200, json={"licenses": ["MIT"]})

    _registry(monkeypatch, slow)
    sbom, purls = _gem_sbom(10)

    scan_id = _run(sync_session, sbom)

    # 0 s -> 10 s -> 20 s (still under 25) -> 30 s: three lookups, then stop.
    assert requests == 3
    cached = _cached(sync_session, purls)
    assert len(cached) == 3
    assert not any(row.is_negative for row in cached.values())
    record = _enrichment(sync_session, scan_id)
    assert record is not None
    assert record["not_looked_up"] == 7
    assert record["not_looked_up_reason"] == "budget_exhausted"
    assert record["spent_seconds"] == 30.0

    # The scan still succeeded with all components stored; only 3 got a licence.
    findings = sync_session.execute(
        select(LicenseFinding).where(
            LicenseFinding.scan_id == scan_id, LicenseFinding.kind == "concluded"
        )
    ).scalars().all()
    assert len(findings) == 3

    # A later scan of the same components resumes where this one stopped.
    requests = 0
    clock.now = 0.0
    monkeypatch.setenv("LICENSE_FETCH_SCAN_BUDGET_SECONDS", "1000")
    scan_two = _run(sync_session, sbom)
    assert requests == 7
    assert len(_cached(sync_session, purls)) == 10
    assert _enrichment(sync_session, scan_two) is None


def test_cache_hits_are_served_after_the_budget_is_spent(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "true")
    monkeypatch.setenv("LICENSE_FETCH_SCAN_BUDGET_SECONDS", "5")
    clock = _Clock()
    monkeypatch.setattr(dispatcher_mod, "time", clock)

    def slow(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        clock.now += 10.0
        return httpx.Response(200, json={"licenses": ["MIT"]})

    _registry(monkeypatch, slow)
    sbom, purls = _gem_sbom(4)
    _run(sync_session, sbom)  # first scan: one lookup spends the budget
    assert len(_cached(sync_session, purls)) == 1

    # The one cached component is answered from the cache in a second scan
    # even though a new budget would refuse a network lookup for it.
    clock.now = 0.0
    scan_two = _run(sync_session, {**sbom, "components": sbom["components"][:1]})
    assert _enrichment(sync_session, scan_two) is None


def test_default_budget_leaves_a_normal_scan_untouched(
    sync_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LICENSE_FETCH_ENABLED", "true")
    monkeypatch.delenv("LICENSE_FETCH_SCAN_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("LICENSE_FETCH_CONSECUTIVE_FAILURE_LIMIT", raising=False)
    _registry(monkeypatch, _ok)
    sbom, purls = _gem_sbom(15)

    scan_id = _run(sync_session, sbom)

    cached = _cached(sync_session, purls)
    assert len(cached) == 15
    assert all(row.spdx_id == "MIT" for row in cached.values())
    assert _enrichment(sync_session, scan_id) is None
