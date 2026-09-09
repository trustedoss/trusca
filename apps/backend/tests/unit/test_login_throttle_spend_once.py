# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
core.login_throttle.spend_once - the real call site for its OWN
`redis_degradation.record()` event (#419/#420).

Unlike `_degraded()` (covered via `seconds_until_retry` in
`test_login_throttle.py` and via `FailOpenRedisStorage` in
`test_rate_limit.py`), `spend_once` calls `record()` directly with a
different event name ("auth.mfa_single_use_not_enforced" rather than the
generic "auth.throttle_unavailable"), because a bare Redis-is-down message
would not tell an incident responder that single-use enforcement was
specifically off. A prior review round found that call site logging through
a bare `log.warning()` of its own, un-deduped - reproducing #419 under this
event's own name. Nothing exercised `spend_once` at all before this file.
"""

from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from core import login_throttle, redis_degradation


async def test_spend_once_degrades_open_when_redis_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _down() -> None:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(login_throttle, "_redis", _down)

    assert await login_throttle.spend_once("some-jti", seconds=60) is True


async def test_spend_once_reports_its_own_event_and_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _down() -> None:
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(login_throttle, "_redis", _down)

    logged: list[dict[str, object]] = []
    monkeypatch.setattr(
        redis_degradation.log,
        "warning",
        lambda event, **kw: logged.append({"event": event, **kw}),
    )

    await login_throttle.spend_once("some-jti", seconds=60)

    assert len(logged) == 1
    assert logged[0]["event"] == "auth.mfa_single_use_not_enforced"
    assert logged[0]["reason"] == "redis unavailable"
    assert logged[0]["window_seconds"] == 60

    snapshot = redis_degradation.snapshot()
    assert snapshot.keys() == {"login_throttle"}
    assert snapshot["login_throttle"]["count"] == 1
