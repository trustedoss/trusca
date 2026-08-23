# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Unit tests for `core.ws_registry` (W4): the Redis-backed WebSocket
connection registry that replaced the per-process in-memory dict in
`api.v1.ws`.

These tests exercise `register_connection` / `touch_connection` /
`unregister_connection` directly against a small in-memory fake client
(implementing exactly the `RegistryRedis` protocol), with an injectable
clock so liveness/staleness scenarios are deterministic rather than relying
on real sleeps. Endpoint-level integration (the WebSocket lifecycle actually
calling these) is covered by `tests/unit/test_ws_helpers.py` (fake broker,
several simulated connections) and `tests/integration/test_ws_scan_progress.py`
(real Redis).

Coverage target: `core/ws_registry.py` at or above the 80% line gate.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest


class _Clock:
    """Mutable clock the fake client reads instead of `time.time()`, so
    liveness-expiry scenarios can be driven deterministically."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeRedis:
    """Minimal in-memory implementation of `core.ws_registry.RegistryRedis`."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.zsets: dict[str, dict[str, float]] = {}
        # value = expiry wall-clock time; matches real Redis EX semantics.
        self.strings: dict[str, float] = {}
        self.published: list[tuple[str, str]] = []

    async def zadd(self, name: str, mapping: dict[str, float]) -> None:
        self.zsets.setdefault(name, {}).update(mapping)

    async def zcard(self, name: str) -> int:
        return len(self.zsets.get(name, {}))

    async def zrange(self, name: str, start: int, end: int) -> list[str]:
        ordered = sorted(self.zsets.get(name, {}).items(), key=lambda kv: kv[1])
        members = [member for member, _score in ordered]
        if end == -1:
            return members[start:]
        return members[start : end + 1]

    async def zrem(self, name: str, *values: str) -> None:
        zset = self.zsets.get(name, {})
        for value in values:
            zset.pop(value, None)

    async def set(self, name: str, value: str, *, ex: int) -> None:
        self.strings[name] = self._clock.now + ex

    async def mget(self, names: list[str]) -> list[str | None]:
        return [
            "1" if (exp := self.strings.get(name)) is not None and exp > self._clock.now else None
            for name in names
        ]

    async def delete(self, *names: str) -> None:
        for name in names:
            self.strings.pop(name, None)

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Key / channel formatting, pure functions.
# ---------------------------------------------------------------------------


def test_user_key_includes_the_user_id() -> None:
    from core.ws_registry import user_key

    uid = uuid.uuid4()
    assert user_key(uid) == f"ws:conns:user:{uid}"


def test_alive_key_includes_the_connection_id() -> None:
    from core.ws_registry import alive_key

    assert alive_key("abc123") == "ws:conns:alive:abc123"


def test_evict_channel_includes_the_connection_id() -> None:
    from core.ws_registry import evict_channel

    assert evict_channel("abc123") == "ws:evict:abc123"


def test_new_connection_id_is_unique() -> None:
    from core.ws_registry import new_connection_id

    ids = {new_connection_id() for _ in range(50)}
    assert len(ids) == 50


# ---------------------------------------------------------------------------
# register_connection, per-user cap
# ---------------------------------------------------------------------------


def test_register_admits_up_to_the_cap_without_eviction() -> None:
    from core.ws_registry import register_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        for i in range(3):
            result = await register_connection(
                client,
                user_id=uid,
                connection_id=f"conn-{i}",
                max_per_user=3,
                max_global=500,
                presence_ttl_seconds=90,
                now=float(i),
            )
            assert result.accepted is True
            assert result.evicted_connection_id is None

    _run(_exercise())
    assert await_zcard(client, f"ws:conns:user:{uid}") == 3


def await_zcard(client: _FakeRedis, key: str) -> int:
    return len(client.zsets.get(key, {}))


def test_register_evicts_the_oldest_by_connect_time_not_registration_order_of_the_call() -> None:
    """The eviction pick is driven by the `now=` score, not by call order.
    Pinning this against explicit timestamps is what caught the real bug
    this module's docstring describes (a shared score field let a heartbeat
    make an older connection look newer)."""
    from core.ws_registry import register_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        await register_connection(
            client,
            user_id=uid,
            connection_id="newest-by-call-order-but-oldest-by-time",
            max_per_user=2,
            max_global=500,
            presence_ttl_seconds=90,
            now=100.0,  # earliest timestamp, even though registered first here
        )
        await register_connection(
            client,
            user_id=uid,
            connection_id="middle",
            max_per_user=2,
            max_global=500,
            presence_ttl_seconds=90,
            now=200.0,
        )
        result = await register_connection(
            client,
            user_id=uid,
            connection_id="third",
            max_per_user=2,
            max_global=500,
            presence_ttl_seconds=90,
            now=300.0,
        )
        assert result.accepted is True
        assert result.evicted_connection_id == "newest-by-call-order-but-oldest-by-time"

    _run(_exercise())


def test_register_with_cap_one_evicts_each_previous_connection() -> None:
    from core.ws_registry import register_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        r1 = await register_connection(
            client,
            user_id=uid,
            connection_id="a",
            max_per_user=1,
            max_global=500,
            presence_ttl_seconds=90,
            now=1.0,
        )
        assert r1.evicted_connection_id is None
        r2 = await register_connection(
            client,
            user_id=uid,
            connection_id="b",
            max_per_user=1,
            max_global=500,
            presence_ttl_seconds=90,
            now=2.0,
        )
        assert r2.evicted_connection_id == "a"
        r3 = await register_connection(
            client,
            user_id=uid,
            connection_id="c",
            max_per_user=1,
            max_global=500,
            presence_ttl_seconds=90,
            now=3.0,
        )
        assert r3.evicted_connection_id == "b"

    _run(_exercise())


def test_register_isolates_users_from_each_other() -> None:
    from core.ws_registry import register_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    async def _exercise() -> None:
        r_a = await register_connection(
            client,
            user_id=user_a,
            connection_id="a",
            max_per_user=1,
            max_global=500,
            presence_ttl_seconds=90,
            now=1.0,
        )
        # user_b has its own bucket, it must NOT evict user_a's connection.
        r_b = await register_connection(
            client,
            user_id=user_b,
            connection_id="b",
            max_per_user=1,
            max_global=500,
            presence_ttl_seconds=90,
            now=2.0,
        )
        assert r_a.evicted_connection_id is None
        assert r_b.evicted_connection_id is None

    _run(_exercise())
    assert await_zcard(client, f"ws:conns:user:{user_a}") == 1
    assert await_zcard(client, f"ws:conns:user:{user_b}") == 1


# ---------------------------------------------------------------------------
# register_connection, global cap
# ---------------------------------------------------------------------------


def test_register_refuses_outright_when_the_global_cap_is_reached() -> None:
    """The global cap NEVER evicts, a refused connection leaves both ZSETs
    exactly as they were (the new connection is not added either)."""
    from core.ws_registry import register_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    async def _exercise() -> None:
        r1 = await register_connection(
            client,
            user_id=user_a,
            connection_id="a",
            max_per_user=8,
            max_global=1,
            presence_ttl_seconds=90,
            now=1.0,
        )
        assert r1.accepted is True

        r2 = await register_connection(
            client,
            user_id=user_b,
            connection_id="b",
            max_per_user=8,
            max_global=1,
            presence_ttl_seconds=90,
            now=2.0,
        )
        assert r2.accepted is False
        assert r2.evicted_connection_id is None

    _run(_exercise())
    assert await_zcard(client, "ws:conns:global") == 1
    assert list(client.zsets["ws:conns:global"]) == ["a"]
    assert await_zcard(client, f"ws:conns:user:{user_b}") == 0


# ---------------------------------------------------------------------------
# touch_connection, liveness only, never the connect-order score
# ---------------------------------------------------------------------------


def test_touch_connection_does_not_change_the_zset_score() -> None:
    """Regression pin for the bug this module's docstring describes: touch
    must refresh ONLY the liveness key, never the ZSET score used to pick
    the oldest connection for eviction."""
    from core.ws_registry import register_connection, touch_connection, user_key

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        await register_connection(
            client,
            user_id=uid,
            connection_id="conn-1",
            max_per_user=8,
            max_global=500,
            presence_ttl_seconds=90,
            now=42.0,
        )
        clock.advance(10)
        await touch_connection(client, connection_id="conn-1", presence_ttl_seconds=90)

    _run(_exercise())
    assert client.zsets[user_key(uid)]["conn-1"] == 42.0


def test_touch_connection_extends_the_liveness_ttl() -> None:
    from core.ws_registry import alive_key, register_connection, touch_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        await register_connection(
            client,
            user_id=uid,
            connection_id="conn-1",
            max_per_user=8,
            max_global=500,
            presence_ttl_seconds=10,
            now=0.0,
        )
        clock.advance(9)  # still alive, original TTL has not expired yet
        assert (await client.mget([alive_key("conn-1")]))[0] == "1"

        await touch_connection(client, connection_id="conn-1", presence_ttl_seconds=10)
        clock.advance(9)  # would have expired WITHOUT the touch above
        assert (await client.mget([alive_key("conn-1")]))[0] == "1"

    _run(_exercise())


# ---------------------------------------------------------------------------
# Dead-entry pruning, an expired liveness key frees up both caps and is
# never itself picked as "the oldest" (it is gone before the pick happens).
# ---------------------------------------------------------------------------


def test_an_expired_connection_is_pruned_and_frees_up_the_cap() -> None:
    from core.ws_registry import register_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        # Cap of 1, short TTL, never touched again, it will go stale.
        await register_connection(
            client,
            user_id=uid,
            connection_id="abandoned",
            max_per_user=1,
            max_global=500,
            presence_ttl_seconds=5,
            now=0.0,
        )
        clock.advance(6)  # past the 5s TTL, "abandoned" is now dead

        result = await register_connection(
            client,
            user_id=uid,
            connection_id="fresh",
            max_per_user=1,
            max_global=500,
            presence_ttl_seconds=5,
            now=6.0,
        )
        # The dead entry was pruned BEFORE the cap check, so admitting
        # "fresh" does not exceed the cap and evicts nobody.
        assert result.accepted is True
        assert result.evicted_connection_id is None

    _run(_exercise())
    assert set(client.zsets[f"ws:conns:user:{uid}"]) == {"fresh"}
    assert set(client.zsets["ws:conns:global"]) == {"fresh"}


# ---------------------------------------------------------------------------
# unregister_connection, idempotent full cleanup
# ---------------------------------------------------------------------------


def test_unregister_removes_from_both_zsets_and_the_liveness_key() -> None:
    from core.ws_registry import alive_key, register_connection, unregister_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        await register_connection(
            client,
            user_id=uid,
            connection_id="conn-1",
            max_per_user=8,
            max_global=500,
            presence_ttl_seconds=90,
            now=0.0,
        )
        await unregister_connection(client, user_id=uid, connection_id="conn-1")

    _run(_exercise())
    assert await_zcard(client, f"ws:conns:user:{uid}") == 0
    assert await_zcard(client, "ws:conns:global") == 0
    assert alive_key("conn-1") not in client.strings


def test_unregister_twice_is_safe() -> None:
    from core.ws_registry import register_connection, unregister_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> None:
        await register_connection(
            client,
            user_id=uid,
            connection_id="conn-1",
            max_per_user=8,
            max_global=500,
            presence_ttl_seconds=90,
            now=0.0,
        )
        await unregister_connection(client, user_id=uid, connection_id="conn-1")
        # Second call must not raise.
        await unregister_connection(client, user_id=uid, connection_id="conn-1")

    _run(_exercise())


# ---------------------------------------------------------------------------
# publish_eviction
# ---------------------------------------------------------------------------


def test_publish_eviction_publishes_the_reason_on_the_connection_channel() -> None:
    from core.ws_registry import evict_channel, publish_eviction

    clock = _Clock()
    client = _FakeRedis(clock)

    _run(publish_eviction(client, "conn-1", reason="newer_connection"))

    assert client.published == [(evict_channel("conn-1"), "newer_connection")]


@pytest.mark.parametrize("presence_ttl_seconds", [1, 90, 3600])
def test_register_connection_never_evicts_the_connection_that_just_registered(
    presence_ttl_seconds: int,
) -> None:
    """Even at the degenerate cap of 0, the newly-registered connection is
    never the one picked for eviction, `register_connection`'s own guard
    (`candidate != connection_id`) exists specifically for this."""
    from core.ws_registry import RegisterResult, register_connection

    clock = _Clock()
    client = _FakeRedis(clock)
    uid = uuid.uuid4()

    async def _exercise() -> RegisterResult:
        return await register_connection(
            client,
            user_id=uid,
            connection_id="solo",
            max_per_user=0,
            max_global=500,
            presence_ttl_seconds=presence_ttl_seconds,
            now=1.0,
        )

    result = _run(_exercise())
    assert result.accepted is True
    assert result.evicted_connection_id is None
