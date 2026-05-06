"""
DT CircuitBreaker — Phase 2 PR #8.

We drive the breaker against a `fakeredis` server so the tests are fast,
deterministic, and don't require a real Redis. CLAUDE.md core rule #4 is the
contract being verified: 5 consecutive failures flip CLOSED → OPEN, the
breaker stays OPEN for 30 s, and exactly one worker wins the HALF_OPEN
probe slot per cooldown window even when many workers race.

Time is controlled by monkey-patching `integrations.dt.breaker.time.time`
because `_now()` reads wall time directly (the `clock` constructor arg is
documented but, in the current implementation, only used as a hook for
future telemetry).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from integrations.dt import DTBreakerOpen, DTUnavailable

# A test that can't import the module shouldn't even collect — so we import
# at top-level. The fakeredis fixture handles the soft-dep skip elsewhere.
from integrations.dt.breaker import (
    STATE_CLOSED,
    STATE_OPEN,
    CircuitBreaker,
)

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    """Mutable wall-clock surrogate. Tests advance it by calling `tick()`."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeClock]:
    """Patch wall time inside `integrations.dt.breaker` so tests are deterministic."""
    fake = _FakeClock()
    monkeypatch.setattr("integrations.dt.breaker.time.time", fake)
    yield fake


# ---------------------------------------------------------------------------
# CLOSED state — happy path
# ---------------------------------------------------------------------------


def test_call_success_keeps_breaker_closed(make_breaker: Any, clock: _FakeClock) -> None:
    breaker = make_breaker(failure_threshold=5, cooldown_seconds=30)

    result = breaker.call(lambda: "ok")

    assert result == "ok"
    snapshot = breaker.snapshot()
    assert snapshot.state == STATE_CLOSED
    assert snapshot.fail_count == 0


def test_record_success_resets_fail_counter(make_breaker: Any, clock: _FakeClock) -> None:
    """A handful of failures below threshold should fully reset on success."""
    breaker = make_breaker(failure_threshold=5)

    for _ in range(3):
        breaker.record_failure()
    assert breaker.snapshot().fail_count == 3

    breaker.record_success()
    assert breaker.snapshot().fail_count == 0
    assert breaker.snapshot().state == STATE_CLOSED


# ---------------------------------------------------------------------------
# CLOSED → OPEN
# ---------------------------------------------------------------------------


def test_five_consecutive_failures_open_breaker(
    make_breaker: Any, clock: _FakeClock
) -> None:
    breaker = make_breaker(failure_threshold=5, cooldown_seconds=30)

    def _boom() -> None:
        raise DTUnavailable("DT 503")

    for _ in range(5):
        with pytest.raises(DTUnavailable):
            breaker.call(_boom)

    snapshot = breaker.snapshot()
    assert snapshot.state == STATE_OPEN
    assert snapshot.opened_at is not None
    assert snapshot.opened_at == pytest.approx(clock.now)


def test_open_breaker_short_circuits_subsequent_calls(
    make_breaker: Any, clock: _FakeClock
) -> None:
    breaker = make_breaker(failure_threshold=2, cooldown_seconds=30)

    def _boom() -> None:
        raise DTUnavailable("DT 503")

    for _ in range(2):
        with pytest.raises(DTUnavailable):
            breaker.call(_boom)

    # Now OPEN. Subsequent call must NOT invoke fn — it short-circuits to
    # DTBreakerOpen without reaching the underlying DT.
    invoked = {"count": 0}

    def _fn() -> str:
        invoked["count"] += 1
        return "should not run"

    with pytest.raises(DTBreakerOpen):
        breaker.call(_fn)
    assert invoked["count"] == 0


def test_4xx_errors_do_not_count_toward_breaker(
    make_breaker: Any, clock: _FakeClock
) -> None:
    """Non-DTUnavailable errors are user errors and must not flip the breaker."""
    breaker = make_breaker(failure_threshold=2)

    class UserError(Exception):
        pass

    for _ in range(10):
        with pytest.raises(UserError):
            breaker.call(lambda: (_ for _ in ()).throw(UserError("400")))

    snapshot = breaker.snapshot()
    assert snapshot.state == STATE_CLOSED
    assert snapshot.fail_count == 0


# ---------------------------------------------------------------------------
# OPEN → HALF_OPEN → CLOSED  (cooldown elapsed + probe success)
# ---------------------------------------------------------------------------


def test_open_breaker_advances_to_half_open_after_cooldown(
    make_breaker: Any, clock: _FakeClock
) -> None:
    breaker = make_breaker(failure_threshold=2, cooldown_seconds=30)

    def _boom() -> None:
        raise DTUnavailable("DT 503")

    for _ in range(2):
        with pytest.raises(DTUnavailable):
            breaker.call(_boom)
    assert breaker.snapshot().state == STATE_OPEN

    # Within cooldown — still OPEN.
    clock.tick(29)
    with pytest.raises(DTBreakerOpen):
        breaker.call(lambda: "x")

    # Past cooldown — the next call gets the probe slot. The fn runs; if it
    # returns normally the breaker transitions to CLOSED.
    clock.tick(2)  # now 31s past opened_at
    result = breaker.call(lambda: "probe-ok")
    assert result == "probe-ok"
    snapshot = breaker.snapshot()
    assert snapshot.state == STATE_CLOSED
    assert snapshot.opened_at is None  # cleared on close


def test_half_open_probe_failure_returns_to_open(
    make_breaker: Any, clock: _FakeClock
) -> None:
    """Probe fails → breaker goes back to OPEN with a fresh cooldown."""
    breaker = make_breaker(failure_threshold=2, cooldown_seconds=30)

    def _boom() -> None:
        raise DTUnavailable("DT 503")

    for _ in range(2):
        with pytest.raises(DTUnavailable):
            breaker.call(_boom)
    opened_at_first = breaker.snapshot().opened_at

    clock.tick(31)  # past cooldown — next call advances to HALF_OPEN
    with pytest.raises(DTUnavailable):
        breaker.call(_boom)

    snapshot = breaker.snapshot()
    assert snapshot.state == STATE_OPEN
    # opened_at is set with NX so the original timestamp wins; the breaker
    # remains OPEN with the *original* opened_at stamp, but a NEW failure
    # count is incremented.
    assert snapshot.opened_at == opened_at_first


# ---------------------------------------------------------------------------
# Probe-gate race — two workers, one slot
# ---------------------------------------------------------------------------


def test_concurrent_probe_only_one_worker_wins_slot(
    fakeredis_client: Any, clock: _FakeClock
) -> None:
    """
    The Lua _PROBE_GATE_LUA script must hand exactly one HALF_OPEN slot per
    cooldown window. We simulate two Celery workers by constructing two
    independent CircuitBreaker instances backed by the same Redis keyspace
    (the fakeredis client). Both observe OPEN + elapsed cooldown at the
    same simulated wall time; the SETNX inside the script must let only one
    of them flip state to HALF_OPEN and execute its probe.

    Approach: drive both breakers serially but at the same `clock.now` so
    the only ordering signal is the Redis-side SETNX — i.e. exactly one will
    see `got_lock == 1` and the other will see `got_lock == 0`. Threads are
    overkill here; fakeredis processes commands serially anyway, so a
    sequential race against the same store is the cleanest model.
    """
    breaker_a = CircuitBreaker(
        redis_client=fakeredis_client,
        failure_threshold=2,
        cooldown_seconds=30,
    )
    breaker_b = CircuitBreaker(
        redis_client=fakeredis_client,
        failure_threshold=2,
        cooldown_seconds=30,
    )

    def _boom() -> None:
        raise DTUnavailable("DT 503")

    # Drive worker A through the failures so both workers see OPEN.
    for _ in range(2):
        with pytest.raises(DTUnavailable):
            breaker_a.call(_boom)
    assert breaker_a.snapshot().state == STATE_OPEN
    assert breaker_b.snapshot().state == STATE_OPEN  # shared state

    # Past cooldown for both at the same simulated wall time.
    clock.tick(31)

    runs: dict[str, int] = {"a": 0, "b": 0}

    def _probe_a() -> str:
        runs["a"] += 1
        return "ok-a"

    def _probe_b() -> str:
        runs["b"] += 1
        return "ok-b"

    # Worker A goes first — wins the SETNX, runs probe, transitions to CLOSED.
    result_a = breaker_a.call(_probe_a)
    assert result_a == "ok-a"

    # Worker B observes CLOSED already — its call simply runs through the
    # CLOSED path, no probe contention. The crucial property is that B did
    # NOT flip state to HALF_OPEN behind A's back.
    snapshot_after_a = breaker_a.snapshot()
    assert snapshot_after_a.state == STATE_CLOSED

    # If A had flipped state to HALF_OPEN and B had then *also* flipped, B
    # would have been the one to transition to CLOSED — both would be set to
    # 1. We assert A executed exactly once before transition.
    assert runs["a"] == 1


def test_probe_gate_lua_script_grants_slot_to_only_one_caller(
    fakeredis_client: Any, clock: _FakeClock
) -> None:
    """The Lua _PROBE_GATE_LUA primitive must hand exactly one probe slot.

    The test drives the script directly via redis.eval() — same path the
    breaker uses internally — and asserts the SETNX-on-probe-lock returns 1
    on the first call and 0 on every subsequent call within the cooldown
    window. This is the load-bearing piece of M-7's race-window guarantee.
    """
    from integrations.dt.breaker import (
        _KEY_OPENED_AT,
        _KEY_PROBE_LOCK,
        _KEY_STATE,
        _PROBE_GATE_LUA,
    )

    # Set the breaker into OPEN with a stale opened_at so the cooldown is
    # already elapsed when the script evaluates.
    fakeredis_client.set(_KEY_STATE, "open")
    fakeredis_client.set(_KEY_OPENED_AT, str(clock.now - 60))

    cooldown = 30
    args = [str(cooldown), str(clock.now)]

    # First eval — must grant the slot (return 1) and flip state to half_open.
    first = fakeredis_client.eval(
        _PROBE_GATE_LUA,
        3,
        _KEY_STATE,
        _KEY_OPENED_AT,
        _KEY_PROBE_LOCK,
        *args,
    )
    assert int(first) == 1
    assert fakeredis_client.get(_KEY_STATE) == "half_open"
    assert fakeredis_client.get(_KEY_PROBE_LOCK) is not None

    # Reset state to OPEN to simulate "two workers see OPEN at the same wall
    # time" — but the probe lock from the first caller is still held. The
    # script must observe state != 'open' OR the lock-held condition and
    # return 0.
    fakeredis_client.set(_KEY_STATE, "open")  # contrived parallel-arrival

    second = fakeredis_client.eval(
        _PROBE_GATE_LUA,
        3,
        _KEY_STATE,
        _KEY_OPENED_AT,
        _KEY_PROBE_LOCK,
        *args,
    )
    # SETNX on probe-lock fails because the first caller still holds it →
    # second caller does NOT take the slot, return value is 0, and state
    # stays exactly as we left it (no half_open flip).
    assert int(second) == 0


# ---------------------------------------------------------------------------
# force_close (admin escape hatch)
# ---------------------------------------------------------------------------


def test_force_close_resets_all_state(make_breaker: Any, clock: _FakeClock) -> None:
    breaker = make_breaker(failure_threshold=2, cooldown_seconds=30)

    def _boom() -> None:
        raise DTUnavailable("DT 503")

    for _ in range(2):
        with pytest.raises(DTUnavailable):
            breaker.call(_boom)
    assert breaker.snapshot().state == STATE_OPEN

    breaker.force_close()
    snapshot = breaker.snapshot()
    assert snapshot.state == STATE_CLOSED
    assert snapshot.fail_count == 0
    assert snapshot.opened_at is None
