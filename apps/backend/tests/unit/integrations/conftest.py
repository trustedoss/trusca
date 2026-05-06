"""
Shared fixtures for `integrations/` unit tests — Phase 2 PR #8.

Goals:
  - Force the scan-backend mock so adapters never invoke real cdxgen / ORT /
    Trivy binaries during unit tests (deterministic + CI-friendly).
  - Provide a `fakeredis` client to drive the CircuitBreaker without a real
    Redis. Each test gets a fresh fake server so breaker state cannot leak.
  - Provide an `httpx.MockTransport`-backed DTClient builder so the breaker /
    health tests can simulate 5xx, 4xx, timeouts, and successful responses
    without booting a real DT.

These fixtures intentionally do NOT touch the FastAPI app or its async engine
— they live alongside Celery-style sync code paths. Async fixtures elsewhere
in the suite stay unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest


@pytest.fixture
def scan_backend_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the cdxgen / ORT / Trivy adapters to use mock fixture JSON.

    Tests that touch any subprocess-driven adapter must opt into this fixture
    so external tools are never spawned. The env var is the canonical knob
    (resolved at call time per CLAUDE.md core rule #11) so we set it via
    monkeypatch and let the adapters read it normally.
    """
    monkeypatch.setenv("TRUSTEDOSS_SCAN_BACKEND", "mock")


@pytest.fixture
def fakeredis_client() -> Iterator[Any]:
    """Yield a fresh `fakeredis` client for breaker-state isolation.

    Importing fakeredis at fixture-scope (rather than module top) keeps the
    test collection light when the dev dependency is missing locally — the
    skip behaviour is captured in the per-test fixture below.
    """
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    try:
        yield client
    finally:
        client.flushall()
        client.close()


@pytest.fixture
def make_breaker(fakeredis_client: Any) -> Callable[..., Any]:
    """Factory returning a CircuitBreaker bound to the fake Redis.

    Tests pass per-case overrides — failure_threshold, cooldown_seconds, or a
    fake clock so they can advance time without `time.sleep`. The factory
    keeps the fake Redis singleton across all breakers in the same test so
    the "two workers race" simulation can construct two CircuitBreaker
    instances backed by the same shared store.
    """
    from integrations.dt.breaker import CircuitBreaker

    def _factory(
        *,
        failure_threshold: int | None = None,
        cooldown_seconds: int | None = None,
        clock: Callable[[], float] | None = None,
    ) -> CircuitBreaker:
        kwargs: dict[str, Any] = {"redis_client": fakeredis_client}
        if failure_threshold is not None:
            kwargs["failure_threshold"] = failure_threshold
        if cooldown_seconds is not None:
            kwargs["cooldown_seconds"] = cooldown_seconds
        if clock is not None:
            kwargs["clock"] = clock
        return CircuitBreaker(**kwargs)

    return _factory


@pytest.fixture
def make_dt_client() -> Callable[[Callable[[httpx.Request], httpx.Response]], Any]:
    """Factory returning a DTClient whose httpx is an `httpx.MockTransport`.

    The handler callback receives every request and returns whatever
    `httpx.Response` the test wants — that lets tests simulate 200 / 4xx / 5xx
    / timeout / connect-error responses deterministically. The DTClient is
    built with a controlled base_url so tests do not depend on `DT_URL`.
    """
    from integrations.dt.client import DTClient

    def _factory(handler: Callable[[httpx.Request], httpx.Response]) -> DTClient:
        transport = httpx.MockTransport(handler)
        http = httpx.Client(
            transport=transport,
            base_url="http://test-dt.invalid",
            headers={"X-API-Key": "test-key", "Accept": "application/json"},
            timeout=1.0,
        )
        return DTClient(http=http)

    return _factory
