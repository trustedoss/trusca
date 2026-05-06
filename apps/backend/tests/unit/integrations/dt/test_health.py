"""
DT health monitor — Phase 2 PR #8.

The heartbeat task lives in `integrations.dt.health.run_health_check`.
Behaviour we pin:

  - Successful heartbeat → breaker.record_success() (drives HALF_OPEN→CLOSED).
  - Failure heartbeat → breaker.record_failure() (drives CLOSED→OPEN at threshold).
  - Auto-restart is a no-op unless `DT_AUTO_RESTART=true` and the breaker has
    been OPEN past the auto-restart threshold (5 cooldown windows).
  - The outcome dict the wrapper Celery task returns matches the ABI Beat /
    admin dashboards consume.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from integrations.dt import DTUnavailable
from integrations.dt.breaker import STATE_CLOSED, STATE_OPEN
from integrations.dt.health import run_health_check

# ---------------------------------------------------------------------------
# Successful heartbeat
# ---------------------------------------------------------------------------


def test_successful_heartbeat_records_success(
    make_breaker: Any, make_dt_client: Any
) -> None:
    breaker = make_breaker(failure_threshold=5, cooldown_seconds=30)

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.12.0"})

    client = make_dt_client(ok)
    try:
        outcome = run_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.healthy is True
    assert outcome.error is None
    assert outcome.snapshot_after.state == STATE_CLOSED
    assert outcome.snapshot_after.fail_count == 0
    assert outcome.auto_restart_attempted is False


def test_heartbeat_after_failures_resets_breaker(
    make_breaker: Any, make_dt_client: Any
) -> None:
    """A successful probe drops the fail counter even from below threshold."""
    breaker = make_breaker(failure_threshold=5)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.snapshot().fail_count == 3

    def ok(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"version": "4.12.0"})

    client = make_dt_client(ok)
    try:
        outcome = run_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.healthy is True
    assert outcome.snapshot_after.fail_count == 0


# ---------------------------------------------------------------------------
# Failed heartbeat
# ---------------------------------------------------------------------------


def test_5xx_heartbeat_records_failure(
    make_breaker: Any, make_dt_client: Any
) -> None:
    breaker = make_breaker(failure_threshold=2, cooldown_seconds=30)

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="dt down")

    client = make_dt_client(boom)
    try:
        outcome = run_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.healthy is False
    assert outcome.error is not None
    # One failure, one below threshold (threshold=2): still CLOSED with count=1.
    assert outcome.snapshot_after.fail_count == 1
    assert outcome.snapshot_after.state == STATE_CLOSED


def test_consecutive_failures_open_breaker_through_health_monitor(
    make_breaker: Any, make_dt_client: Any
) -> None:
    breaker = make_breaker(failure_threshold=3, cooldown_seconds=30)

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="dt 500")

    client = make_dt_client(boom)
    try:
        for _ in range(3):
            outcome = run_health_check(breaker=breaker, client=client)
            assert outcome.healthy is False
    finally:
        client.close()

    snapshot = breaker.snapshot()
    assert snapshot.state == STATE_OPEN
    assert snapshot.fail_count >= 3


def test_timeout_heartbeat_records_failure(
    make_breaker: Any, make_dt_client: Any
) -> None:
    breaker = make_breaker(failure_threshold=2, cooldown_seconds=30)

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated")

    client = make_dt_client(timeout)
    try:
        outcome = run_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.healthy is False
    assert outcome.snapshot_after.fail_count == 1


# ---------------------------------------------------------------------------
# Auto-restart gate
# ---------------------------------------------------------------------------


def test_auto_restart_disabled_by_default(
    make_breaker: Any, make_dt_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DT_AUTO_RESTART defaults to false → auto_restart_attempted == False."""
    monkeypatch.delenv("DT_AUTO_RESTART", raising=False)
    breaker = make_breaker(failure_threshold=1, cooldown_seconds=30)

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="dead")

    client = make_dt_client(boom)
    try:
        outcome = run_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.snapshot_after.state == STATE_OPEN
    assert outcome.auto_restart_attempted is False


def test_auto_restart_only_fires_after_extended_open_window(
    make_breaker: Any,
    make_dt_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Even with DT_AUTO_RESTART=true, the monitor must wait for the breaker to
    have been OPEN past 5x cooldown_seconds before invoking docker.

    We force the breaker into OPEN with an old `opened_at` and assert the
    `_attempt_auto_restart` hook is reached. We monkeypatch
    `_attempt_auto_restart` to just record that it was called — the real
    implementation shells out to docker, which we won't drive from a unit
    test.
    """
    monkeypatch.setenv("DT_AUTO_RESTART", "true")
    # `_has_been_open_long_enough` reads `dt_breaker_cooldown_seconds()` at
    # call time (CLAUDE.md core rule #11) — so we drop the env-side value to
    # 10s rather than override the breaker constructor argument, which only
    # affects the breaker's own threshold logic.
    monkeypatch.setenv("DT_BREAKER_COOLDOWN_SECONDS", "10")
    breaker = make_breaker(failure_threshold=1, cooldown_seconds=10)

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="dead")

    # Push the breaker into OPEN with opened_at far enough in the past that
    # _has_been_open_long_enough returns True. We do that by record_failure +
    # then patching opened_at directly via the Redis client.
    breaker.record_failure()
    assert breaker.snapshot().state == STATE_OPEN
    # Auto-restart fires after 5 * cooldown = 50s. Backdate by 120s so we are
    # comfortably past that threshold even with arithmetic skew.
    fake_redis = breaker._client()  # type: ignore[attr-defined]
    import time as _time

    fake_redis.set("dt:breaker:opened_at", str(_time.time() - 120))

    invocations = {"count": 0}

    def fake_attempt() -> bool:
        invocations["count"] += 1
        return True

    monkeypatch.setattr(
        "integrations.dt.health._attempt_auto_restart", fake_attempt
    )

    client = make_dt_client(boom)
    try:
        outcome = run_health_check(breaker=breaker, client=client)
    finally:
        client.close()

    assert outcome.snapshot_after.state == STATE_OPEN
    assert invocations["count"] == 1
    assert outcome.auto_restart_attempted is True


def test_dt_unavailable_propagation_via_record_failure(
    make_breaker: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When client.health() raises DTUnavailable directly, the monitor still
    records a failure and reports unhealthy.

    This branch covers the path where the underlying client returns the
    Python exception (not just a 5xx response object).
    """
    breaker = make_breaker(failure_threshold=2)

    class _Boom:
        def health(self) -> dict[str, str]:
            raise DTUnavailable("network kaput")

        def close(self) -> None:
            pass

    outcome = run_health_check(breaker=breaker, client=_Boom())  # type: ignore[arg-type]

    assert outcome.healthy is False
    assert outcome.snapshot_after.fail_count == 1
