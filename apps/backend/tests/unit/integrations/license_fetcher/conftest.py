"""
Shared fixtures for the license-fetcher unit suite.

The fetchers are intentionally simple synchronous adapters built on
``httpx.Client``; we drive them with ``httpx.MockTransport`` (the same
pattern the DT client tests use) so external network is never touched.

We also expose a ``no_throttle`` fixture that monkeypatches
``time.sleep`` to a no-op for the duration of the test — the per-host
gate inside ``base.request_with_retry`` would otherwise serialise our
1-req/sec test calls and slow the suite to a crawl.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest


@pytest.fixture
def no_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip per-host minimum-interval sleeps inside the fetcher.

    The retry helper accepts a ``sleep`` callable so production code
    can wait between retries; in tests we replace ``time.sleep`` (the
    module-level default the fetcher modules pull at call time) with
    a no-op so each MockTransport-driven assertion stays deterministic
    and fast.
    """
    # Replace the ``time`` module in the base module's namespace with
    # a stub whose ``.sleep`` is a no-op. ``monotonic`` still needs to
    # advance, so we delegate to the real implementation.
    import time as real_time

    import integrations.license_fetcher.base as base_mod

    class _NoSleepTime:
        @staticmethod
        def sleep(_seconds: float) -> None:
            return None

        monotonic = staticmethod(real_time.monotonic)

    monkeypatch.setattr(base_mod, "time", _NoSleepTime)
    # Also clear any per-host gate state that bled in from a previous
    # test so concurrent test runs do not race on the module-global
    # ``_HOST_LOCKS`` dict.
    base_mod._HOST_LOCKS.clear()


@pytest.fixture
def make_mock_client() -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.Client]:
    """Factory: build an ``httpx.Client`` whose transport is a ``MockTransport``."""

    def _factory(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=1.0,
            follow_redirects=True,
        )

    return _factory


@pytest.fixture
def call_log() -> Iterator[list[tuple[str, str]]]:
    """Reusable list[(method, url)] log for fetcher assertion."""
    log: list[tuple[str, str]] = []
    yield log


@pytest.fixture
def session_factory() -> Iterator[Any]:
    """In-memory dispatcher session — see test_dispatch.py for usage."""
    yield None
