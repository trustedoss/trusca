"""
DT outage regression — CLAUDE.md core rule #4.

When DT is unhealthy:
  1. Five consecutive 5xx responses (or timeouts) flip the breaker to OPEN.
  2. Subsequent DT calls short-circuit with `DTBreakerOpen` instead of
     consuming socket budget.
  3. The portal continues to serve cached vulnerability data from the local
     `vulnerabilities` table — the dt_resync task is the writer; on the read
     path, the scan pipeline persists VulnerabilityFinding rows that
     reference the cached Vulnerability ids regardless of DT health.

We don't need a real Postgres or DT to verify the breaker → cache contract:
fakeredis-backed CircuitBreaker + httpx.MockTransport-driven DTClient is
enough. This test makes the regression visible at the integration layer
(both pieces wired together) without booting either dependency.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

from integrations.dt import DTBreakerOpen, DTUnavailable
from integrations.dt.breaker import STATE_CLOSED, STATE_OPEN

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    fake = _FakeClock()
    monkeypatch.setattr("integrations.dt.breaker.time.time", fake)
    return fake


@pytest.fixture
def fakeredis_client() -> Any:
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    try:
        yield client
    finally:
        client.flushall()
        client.close()


def _make_dt_client(handler) -> Any:  # type: ignore[no-untyped-def]
    from integrations.dt.client import DTClient

    transport = httpx.MockTransport(handler)
    http = httpx.Client(
        transport=transport,
        base_url="http://test-dt.invalid",
        headers={"X-API-Key": "test", "Accept": "application/json"},
        timeout=1.0,
    )
    return DTClient(http=http)


# ---------------------------------------------------------------------------
# OPEN after 5 consecutive 5xx
# ---------------------------------------------------------------------------


def test_five_consecutive_dt_5xx_opens_breaker_and_short_circuits(
    fakeredis_client: Any, clock: _FakeClock
) -> None:
    from integrations.dt.breaker import CircuitBreaker

    breaker = CircuitBreaker(
        redis_client=fakeredis_client,
        failure_threshold=5,
        cooldown_seconds=30,
    )

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="dt down")

    client = _make_dt_client(boom)
    try:
        # 5 consecutive 5xx → OPEN
        for _ in range(5):
            with pytest.raises(DTUnavailable):
                breaker.call(client.health)
        assert breaker.snapshot().state == STATE_OPEN

        # Subsequent calls short-circuit without hitting the wire.
        request_count = {"value": 0}

        def trap(_request: httpx.Request) -> httpx.Response:
            request_count["value"] += 1
            return httpx.Response(200, json={"version": "x"})

        # Replace the transport handler — but the breaker MUST short-circuit
        # before any request goes out, so the trap handler should never run.
        client_2 = _make_dt_client(trap)
        try:
            with pytest.raises(DTBreakerOpen):
                breaker.call(client_2.health)
            assert request_count["value"] == 0
        finally:
            client_2.close()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Recovery — DT comes back, cooldown elapses, probe succeeds
# ---------------------------------------------------------------------------


def test_dt_recovery_via_half_open_probe(
    fakeredis_client: Any, clock: _FakeClock
) -> None:
    from integrations.dt.breaker import CircuitBreaker

    breaker = CircuitBreaker(
        redis_client=fakeredis_client,
        failure_threshold=3,
        cooldown_seconds=30,
    )

    # Phase 1: DT is down.
    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="dt down")

    bad_client = _make_dt_client(boom)
    try:
        for _ in range(3):
            with pytest.raises(DTUnavailable):
                breaker.call(bad_client.health)
    finally:
        bad_client.close()
    assert breaker.snapshot().state == STATE_OPEN

    # Phase 2: cooldown elapses, DT comes back.
    clock.tick(31)

    def ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.12.0"})

    good_client = _make_dt_client(ok)
    try:
        result = breaker.call(good_client.health)
        assert result == {"version": "4.12.0"}
    finally:
        good_client.close()
    assert breaker.snapshot().state == STATE_CLOSED


# ---------------------------------------------------------------------------
# Vulnerability cache reads independent of DT health
# ---------------------------------------------------------------------------


def test_vulnerability_cache_query_does_not_consult_dt() -> None:
    """The portal's `vulnerabilities` table is the read path during outages.

    This is a model-layer smoke test: the row shape is expressive enough to
    render a finding's metadata (severity / summary / details / refs) from
    the cache alone. The actual rendering is covered by the API tests once
    the read endpoints land in PR #9; here we just assert the columns the
    UI relies on can hold the values DT supplies.
    """
    from models import Vulnerability

    vuln = Vulnerability(
        external_id="CVE-2024-MOCK-0001",
        source="trivy",
        severity="high",
        summary="cached summary",
        details="cached details",
        references=[{"name": "NVD", "url": "https://example.invalid/cve"}],
    )
    # Plain attribute access — no DB round-trip.
    assert vuln.severity == "high"
    assert vuln.summary == "cached summary"
    assert vuln.references[0]["name"] == "NVD"


def test_finding_fixed_version_is_a_cached_column_readable_without_dt() -> None:
    """v2.2 2.2-a1 — ``fixed_version`` lives on the cached per-finding row, so
    the OPEN-breaker read path (component drawer / vuln detail) serves it from
    Postgres without ever calling DT.

    The collection of ``fixed_version`` happens at scan time through the breaker
    (``_persist_findings`` runs after the gated DT poll); the *read* services
    (``get_vulnerability_detail`` / ``get_component_detail``) select the column
    straight off ``vulnerability_findings`` with no DT dependency. This model-
    layer smoke test mirrors ``test_vulnerability_cache_query_does_not_consult_dt``
    and pins that the column holds the value the UI renders during an outage.
    """
    from models import VulnerabilityFinding

    finding = VulnerabilityFinding(
        scan_id=uuid.uuid4(),
        component_version_id=uuid.uuid4(),
        vulnerability_id=uuid.uuid4(),
        status="new",
        fixed_version="2.17.1",
    )
    # Plain attribute access — no DB round-trip, no DT call.
    assert finding.fixed_version == "2.17.1"
