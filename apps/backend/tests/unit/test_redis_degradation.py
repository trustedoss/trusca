# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
core.redis_degradation - the shared dedup/tracking behind #419 and #420.

#419: sustained traffic during a Redis outage must not turn into one WARNING
log line per request. #420: a persistent degradation (e.g. a misconfigured
REDIS_URL) must be visible as a count + timestamp, not only as whatever a
single live ping happens to see.
"""

from __future__ import annotations

import time

import pytest

from core import redis_degradation


@pytest.fixture(autouse=True)
def _clean_state():
    redis_degradation._reset_for_tests()
    yield
    redis_degradation._reset_for_tests()


def test_the_first_degraded_call_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        redis_degradation.log,
        "warning",
        lambda event, **kw: logged.append({"event": event, **kw}),
    )

    redis_degradation.record(
        component="ratelimit",
        event="ratelimit.storage_unavailable",
        action="incr",
        exc=RuntimeError("boom"),
    )

    assert len(logged) == 1
    assert logged[0]["event"] == "ratelimit.storage_unavailable"
    assert logged[0]["suppressed_since_last_log"] == 0


def test_a_second_call_within_the_interval_is_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the whole point of #419: two calls close together must not
    produce two log lines."""
    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        redis_degradation.log,
        "warning",
        lambda event, **kw: logged.append({"event": event, **kw}),
    )

    for _ in range(5):
        redis_degradation.record(
            component="ratelimit",
            event="ratelimit.storage_unavailable",
            action="incr",
            exc=RuntimeError("boom"),
        )

    assert len(logged) == 1, "5 calls inside one interval must log once, not 5 times"


def test_the_next_call_after_the_interval_logs_again_with_the_suppressed_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        redis_degradation.log,
        "warning",
        lambda event, **kw: logged.append({"event": event, **kw}),
    )
    fake_now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    redis_degradation.record(
        component="ratelimit", event="ev", action="incr", exc=RuntimeError("a")
    )
    redis_degradation.record(
        component="ratelimit", event="ev", action="incr", exc=RuntimeError("b")
    )
    redis_degradation.record(
        component="ratelimit", event="ev", action="incr", exc=RuntimeError("c")
    )
    fake_now[0] += redis_degradation._LOG_INTERVAL_SECONDS + 1
    redis_degradation.record(
        component="ratelimit", event="ev", action="incr", exc=RuntimeError("d")
    )

    assert len(logged) == 2
    # Two calls happened between the first (logged) and the fourth (logged):
    # the count the operator lost to dedup must not be silently dropped.
    assert logged[1]["suppressed_since_last_log"] == 2


def test_different_actions_are_deduped_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#419 asks for dedup "per action" - the incr path degrading must not
    silence the get path's own first (and therefore worth seeing) failure."""
    logged: list[str] = []
    monkeypatch.setattr(
        redis_degradation.log,
        "warning",
        lambda event, **kw: logged.append(f"{event}:{kw['action']}"),
    )

    redis_degradation.record(
        component="ratelimit", event="ev", action="incr", exc=RuntimeError()
    )
    redis_degradation.record(
        component="ratelimit", event="ev", action="get", exc=RuntimeError()
    )

    assert logged == ["ev:incr", "ev:get"]


def test_snapshot_is_empty_when_nothing_degraded() -> None:
    assert redis_degradation.snapshot() == {}


def test_snapshot_aggregates_actions_under_one_component() -> None:
    redis_degradation.record(
        component="login_throttle", event="ev", action="gate", exc=RuntimeError()
    )
    redis_degradation.record(
        component="login_throttle",
        event="ev",
        action="record_failure",
        exc=RuntimeError(),
    )

    snap = redis_degradation.snapshot()
    assert set(snap.keys()) == {"login_throttle"}
    assert snap["login_throttle"]["count"] == 2


def test_snapshot_keeps_components_separate() -> None:
    redis_degradation.record(
        component="ratelimit", event="ev", action="incr", exc=RuntimeError()
    )
    redis_degradation.record(
        component="login_throttle", event="ev", action="gate", exc=RuntimeError()
    )

    snap = redis_degradation.snapshot()
    assert snap["ratelimit"]["count"] == 1
    assert snap["login_throttle"]["count"] == 1


def test_snapshot_reports_the_most_recent_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_now = [500.0]
    monkeypatch.setattr(time, "time", lambda: fake_now[0])

    redis_degradation.record(
        component="ratelimit", event="ev", action="incr", exc=RuntimeError()
    )
    fake_now[0] = 900.0
    redis_degradation.record(
        component="ratelimit", event="ev", action="get", exc=RuntimeError()
    )

    assert redis_degradation.snapshot()["ratelimit"]["last_degraded_at"] == 900.0
