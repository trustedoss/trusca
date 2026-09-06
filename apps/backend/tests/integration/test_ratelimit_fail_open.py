# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""A Redis outage must not turn every rate-limited endpoint into a 500 (#399).

Before `FailOpenRedisStorage`, slowapi's default construction re-raised
whatever its storage raised, and the header-injection code that ran on the
`swallow_errors=True` path 500'd too, on an `AttributeError` this time rather
than a `RedisError` (see `core/ratelimit.py`'s module docstring for how that
was confirmed). Either way, a limiter meant to protect the service from one
noisy caller became a single point of failure for the whole service instead.

These tests break only the rate limiter's own storage, leaving the real
Redis the rest of the app (including `login_throttle`) is still driven
against untouched. `test_login_throttle.py::test_a_redis_outage_leaves_sign_in_working`
does the mirror image: it breaks `login_throttle`'s Redis client and leaves
the rate limiter working. Neither test's pass depends on the other
mechanism also being broken, which is the point -- a limiter that is merely
disabled for the test looks identical to one that fails open on its own, and
only exercising each in isolation tells the two apart.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient

from tests._db_required import migrate_to_head
from tests._helpers import strong_password, unique_suffix

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module", autouse=True)
def _migrate_once() -> None:
    migrate_to_head()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from main import app as fastapi_app

    # raise_app_exceptions=False: a regression here is a 500, and the test
    # needs to see that status code rather than have httpx re-raise it.
    transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture
def unreachable_ratelimit_storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point the live `limiter` at a Redis nothing is listening on.

    Swaps the storage the strategy actually calls (`limiter._limiter.storage`)
    as well as `limiter._storage` (read by `.check()` / `.reset()`), since
    slowapi keeps two references to the same object and only one of them is
    on `hit()`'s call path. `monkeypatch` restores both when the test ends,
    so the shared singleton is never left pointed at a dead host for the
    tests that run after this one.
    """
    from core.ratelimit import FailOpenRedisStorage, limiter

    broken = FailOpenRedisStorage(
        "redis+failopen://127.0.0.1:1/0",
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
    )
    monkeypatch.setattr(limiter, "_storage", broken)
    monkeypatch.setattr(limiter._limiter, "storage", broken)  # type: ignore[attr-defined]
    yield


async def test_registration_succeeds_when_the_rate_limiter_cannot_reach_redis(
    client: AsyncClient, unreachable_ratelimit_storage: None
) -> None:
    """`/auth/register` is decorated with `@limiter.limit(...)` and nothing
    else that talks to Redis, so a non-201 here is the rate limiter's doing.
    """
    email = f"ratelimit-outage-{unique_suffix()}@example.com"
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": strong_password(), "full_name": "Outage Test"},
    )
    assert response.status_code == 201, response.text


async def test_login_is_not_blocked_by_a_rate_limiter_outage(
    client: AsyncClient, unreachable_ratelimit_storage: None
) -> None:
    """`/auth/login` is decorated with the rate limiter *and* gated by
    `login_throttle`. With only the rate limiter's storage broken, a wrong
    password must still come back as an ordinary 401 -- proof that the
    limiter let the request through rather than 500ing on the way in, and
    that `login_throttle`'s own (unbroken) Redis path is what actually
    handled the attempt.
    """
    email = f"ratelimit-outage-login-{unique_suffix()}@example.com"
    password = strong_password()
    created = await client.post(
        "/auth/register",
        json={"email": email, "password": password, "full_name": "Outage Test"},
    )
    assert created.status_code == 201, created.text

    wrong = await client.post(
        "/auth/login", json={"email": email, "password": "definitely not it 1"}
    )
    assert wrong.status_code == 401, wrong.text

    right = await client.post("/auth/login", json={"email": email, "password": password})
    assert right.status_code == 200, right.text
