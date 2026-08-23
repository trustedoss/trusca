# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Redis-backed WebSocket connection registry (W4).

`core.concurrency-scaling-plan-2026-08-22.md` (trusca-internal) §1.7 traced a
correctness defect, not a capacity one: the per-user connection cap in
`api/v1/ws.py` used to live in a plain process-local dict, so whether a given
user's Nth socket got evicted depended on which uvicorn worker (or which pod)
it happened to land on. The same open-two-tabs action could succeed or fail
depending on deployment shape and luck.

This module replaces the process-local dict with two Redis sorted sets that
every backend process reads and writes, so admission is exact everywhere the
same Redis instance is reachable (already a hard dependency of this endpoint;
see `api/v1/ws.py._redis_pubsub`):

  - `ws:conns:global`       one entry per open connection, any user.
  - `ws:conns:user:{uid}`   one entry per open connection, that user only.

Both are ZSETs keyed by a per-connection id, scored by CONNECT time. The
score is written exactly once, at registration, and never touched again.
That is what "oldest" means for eviction, and it is deliberately kept
separate from liveness. An earlier version of this module reused one score
field for both "connect order" and "still alive" (bumping it on every
heartbeat); that collapsed the two concepts and picked the WRONG connection
to evict whenever a heartbeat landed between two other connections'
registrations (a live connection's periodic touch made it look "newer" than
one that had registered after it but not yet had a heartbeat). Liveness is
tracked separately below.

Liveness: a plain Redis key per connection, `ws:conns:alive:{cid}`, holding
no meaningful value and living entirely on Redis's own `EX` expiry. Each
open connection refreshes its own key on
`core.config.WEBSOCKET_PRESENCE_HEARTBEAT_SECONDS` (needed because a scan
detail tab can sit open and silent for a long time after the scan finishes,
so liveness cannot be inferred from message traffic). A ZSET member whose
alive key has expired is an abandoned socket left behind by a crashed worker
or a killed pod; the next process that happens to run a cap check prunes it
(nothing owns the reaping). A connection that loses a race with pruning (its
alive key expires between two of its own heartbeats, or a check runs between
its registration and its first heartbeat) is NOT resurrected: it simply
stops counting toward the caps until it disconnects and unregisters. That is
the safe direction for this to fail: the caps undercount rather than a live
socket getting closed for a reason nobody published.

Two different caps, two different remedies:

  - Per-user cap exceeded -> evict the user's own oldest connection. This
    mirrors what happened before (open a 3rd tab, the 1st tab's socket
    dies), so it stays an eviction: the newly-admitted connection publishes
    a notice on `evict_channel(evicted_id)` and the evicted connection's OWN
    asyncio task (wherever it lives) is what actually calls
    `WebSocket.close()`. No process ever closes a socket it did not open;
    that is what makes the eviction path safe to run across processes (and,
    as a side effect, across the asyncio-per-task boundary the Starlette
    TestClient already imposes within a single test process, see the
    `tests/integration/test_ws_scan_progress.py` skip note this unit
    removes).
  - Global cap exceeded -> refuse the new connection outright. Evicting an
    unrelated user's live socket just because a stranger asked for capacity
    would be a worse surprise than saying "not now"; the caller closes the
    connection it is already holding, same task, no cross-process signal
    needed.

No Lua scripting: the register/prune/evict sequence below is a handful of
plain commands rather than one atomic script. That accepts a small race
window between concurrent connects from the same user landing on different
processes at nearly the same instant, where the cap could be exceeded by one
or the "oldest wins" pick could be slightly off. This is a soft abuse guard,
not a security boundary (CLAUDE.md quality standard §3 draws that line at
authn/authz and rate limits, and this is neither). Reaching for a scripting
pattern with no precedent elsewhere in this codebase to close a rare
off-by-one is not worth the added surface.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

GLOBAL_KEY = "ws:conns:global"
USER_KEY_PREFIX = "ws:conns:user:"
ALIVE_KEY_PREFIX = "ws:conns:alive:"
EVICT_CHANNEL_PREFIX = "ws:evict:"


class RegistryRedis(Protocol):
    """The subset of the redis-py async client this module calls.

    A `Protocol` rather than importing `redis.asyncio.Redis` directly keeps
    the unit test double (`tests/unit/test_ws_helpers.py`) from needing the
    real client's full surface.
    """

    async def zadd(self, name: str, mapping: dict[str, float]) -> Any: ...

    async def zcard(self, name: str) -> int: ...

    async def zrange(self, name: str, start: int, end: int) -> list[Any]: ...

    async def zrem(self, name: str, *values: str) -> Any: ...

    async def set(self, name: str, value: str, *, ex: int) -> Any: ...

    async def mget(self, names: list[str]) -> list[Any]: ...

    async def delete(self, *names: str) -> Any: ...

    async def publish(self, channel: str, message: str) -> Any: ...


def user_key(user_id: uuid.UUID) -> str:
    """The per-user ZSET key for `user_id`."""
    return f"{USER_KEY_PREFIX}{user_id}"


def alive_key(connection_id: str) -> str:
    """The liveness key for `connection_id` (existence = alive, TTL-backed)."""
    return f"{ALIVE_KEY_PREFIX}{connection_id}"


def evict_channel(connection_id: str) -> str:
    """The pub/sub channel a connection subscribes to for its own eviction.

    Every open connection subscribes to its own channel from the moment it
    registers, so a publish here reaches the connection wherever it lives,
    on whichever process, without that process ever needing to be told
    which one.
    """
    return f"{EVICT_CHANNEL_PREFIX}{connection_id}"


def new_connection_id() -> str:
    """A fresh per-connection identifier, distinct from the user id."""
    return uuid.uuid4().hex


def _decode(member: Any) -> str:
    return member.decode() if isinstance(member, bytes) else str(member)


@dataclass(frozen=True)
class RegisterResult:
    """Outcome of `register_connection`.

    `accepted=False` means the connection was NOT added to either ZSET; the
    caller must close its own socket (global cap, close 4429). `accepted=True`
    with `evicted_connection_id` set means the new connection WAS admitted
    and, as a side effect, pushed the user over their per-user cap; the
    caller must publish an eviction notice for that id (close 1001) but must
    NOT try to close it directly.
    """

    accepted: bool
    evicted_connection_id: str | None


async def _prune_dead(client: RegistryRedis, key: str) -> None:
    """Drop any ZSET member whose liveness key has expired.

    One `ZRANGE` + one `MGET` regardless of set size: cheap at the
    connection-count scale this module bounds (hundreds, per
    `websocket_max_connections_global`'s docstring), and this only runs at
    registration time, never in the per-message forward path.
    """
    members_raw = await client.zrange(key, 0, -1)
    if not members_raw:
        return
    members = [_decode(m) for m in members_raw]
    flags = await client.mget([alive_key(m) for m in members])
    dead = [member for member, flag in zip(members, flags, strict=True) if flag is None]
    if dead:
        await client.zrem(key, *dead)


async def register_connection(
    client: RegistryRedis,
    *,
    user_id: uuid.UUID,
    connection_id: str,
    max_per_user: int,
    max_global: int,
    presence_ttl_seconds: int,
    now: float | None = None,
) -> RegisterResult:
    """Admit `connection_id`, evicting the user's oldest one if it overflows.

    Order of operations: prune dead entries from both sets first (an
    abandoned connection must not count against either cap), THEN check the
    global cap, THEN admit (recording connect time as the ZSET score and
    starting this connection's liveness key), THEN check the per-user cap.
    The new connection is always the highest score in its own user set
    immediately after ZADD, so `ZRANGE ukey 0 0` (the lowest score) can only
    ever pick a *different* connection to evict, never the one that just
    registered.
    """
    ts = now if now is not None else time.time()
    ukey = user_key(user_id)

    await _prune_dead(client, GLOBAL_KEY)
    await _prune_dead(client, ukey)

    global_count = await client.zcard(GLOBAL_KEY)
    if global_count >= max_global:
        return RegisterResult(accepted=False, evicted_connection_id=None)

    await client.zadd(GLOBAL_KEY, {connection_id: ts})
    await client.zadd(ukey, {connection_id: ts})
    await client.set(alive_key(connection_id), "1", ex=presence_ttl_seconds)

    evicted: str | None = None
    user_count = await client.zcard(ukey)
    if user_count > max_per_user:
        oldest = await client.zrange(ukey, 0, 0)
        if oldest:
            candidate = _decode(oldest[0])
            if candidate != connection_id:
                await client.zrem(ukey, candidate)
                await client.zrem(GLOBAL_KEY, candidate)
                evicted = candidate

    return RegisterResult(accepted=True, evicted_connection_id=evicted)


async def touch_connection(
    client: RegistryRedis, *, connection_id: str, presence_ttl_seconds: int
) -> None:
    """Refresh `connection_id`'s liveness key so it is not pruned as dead.

    Deliberately does NOT touch either ZSET's score: the score is connect
    time, fixed at registration, and stays that way for the connection's
    whole life (see module docstring for why an earlier version that also
    bumped the score here picked the wrong connection to evict).
    """
    await client.set(alive_key(connection_id), "1", ex=presence_ttl_seconds)


async def unregister_connection(
    client: RegistryRedis, *, user_id: uuid.UUID, connection_id: str
) -> None:
    """Remove `connection_id` from both ZSETs and drop its liveness key.

    Idempotent: `ZREM`/`DELETE` on an absent member is a no-op, so calling
    this twice (or after the entry was already pruned as dead, or already
    evicted by someone else) is always safe.
    """
    ukey = user_key(user_id)
    await client.zrem(ukey, connection_id)
    await client.zrem(GLOBAL_KEY, connection_id)
    await client.delete(alive_key(connection_id))


async def publish_eviction(client: RegistryRedis, connection_id: str, *, reason: str) -> None:
    """Notify whichever process holds `connection_id` that it lost its cap."""
    await client.publish(evict_channel(connection_id), reason)
