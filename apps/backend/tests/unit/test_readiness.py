"""
Unit tests for the schema-readiness logic behind GET /health/ready (B1).

Both branches are driven without a live Postgres by injecting the two data
sources (expected Alembic HEAD set + DB ``alembic_version`` set) into
``check_schema_readiness``:

  * at-head  → ReadinessResult.ready is True (the route returns 200)
  * behind / branch-mismatch / empty-table / DB-error → ready is False
    (the route returns 503 + RFC 7807 problem+json)

The route itself is exercised end-to-end (200 + 503 envelope) via the ASGI
client with the readiness check monkeypatched, so the problem+json contract is
covered without depending on the snapshot of migrations in the repo.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError

from core.readiness import (
    ReadinessResult,
    check_redis_status,
    check_schema_readiness,
    compute_expected_heads,
)

# ---------------------------------------------------------------------------
# compute_expected_heads — runs against the real in-repo alembic script tree.
# ---------------------------------------------------------------------------


def test_compute_expected_heads_returns_nonempty_sorted() -> None:
    heads = compute_expected_heads()
    assert heads, "the repo's alembic tree must have at least one head"
    assert list(heads) == sorted(heads), "heads must be returned sorted"
    assert all(isinstance(h, str) and h for h in heads)


# ---------------------------------------------------------------------------
# check_redis_status - a fake `redis.asyncio.Redis` swapped in for the real
# client so the branch is driven without a live Redis.
# ---------------------------------------------------------------------------


class _FakeRedisClient:
    def __init__(self, *, ping_result: bool | Exception) -> None:
        self.ping_result = ping_result
        self.ping_calls = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        if isinstance(self.ping_result, Exception):
            raise self.ping_result
        return self.ping_result


def _fake_redis(ping_result: bool | Exception):
    """A `core.readiness.Redis` stand-in that counts `from_url` calls.

    Reused-client tests assert `from_url_calls == 1` after multiple
    `check_redis_status()` calls in the same test (one event loop, per
    `asyncio_default_fixture_loop_scope = "function"`), so a regression back
    to opening a fresh connection per call fails loudly rather than merely
    slowly.
    """
    client = _FakeRedisClient(ping_result=ping_result)

    class _FakeRedis:
        from_url_calls = 0

        @staticmethod
        def from_url(*_args: object, **_kwargs: object) -> _FakeRedisClient:
            _FakeRedis.from_url_calls += 1
            return client

    return _FakeRedis, client


@pytest.fixture(autouse=True)
def _reset_readiness_redis_cache():
    """Each test gets its own event loop, but clear the cache defensively.

    `core.readiness._clients` is keyed by event loop and would naturally miss
    on a fresh loop anyway; clearing `_loopless_client` too keeps a test that
    happens to run outside a loop (none here do) from picking up a fake
    client instance left behind by a previous test.
    """
    import core.readiness as readiness_mod

    readiness_mod._clients.clear()
    readiness_mod._loopless_client = None
    yield
    readiness_mod._clients.clear()
    readiness_mod._loopless_client = None


async def test_check_redis_status_ok_when_ping_succeeds(monkeypatch) -> None:
    fake_redis, client = _fake_redis(ping_result=True)
    monkeypatch.setattr("core.readiness.Redis", fake_redis)

    status = await check_redis_status()

    assert status == "ok"
    assert client.ping_calls == 1


async def test_check_redis_status_degraded_when_ping_returns_false(monkeypatch) -> None:
    fake_redis, _client = _fake_redis(ping_result=False)
    monkeypatch.setattr("core.readiness.Redis", fake_redis)

    assert await check_redis_status() == "degraded"


async def test_check_redis_status_degraded_when_ping_raises(monkeypatch) -> None:
    fake_redis, client = _fake_redis(ping_result=RedisError("connection refused"))
    monkeypatch.setattr("core.readiness.Redis", fake_redis)

    assert await check_redis_status() == "degraded"
    assert client.ping_calls == 1


async def test_check_redis_status_degraded_when_connect_raises(monkeypatch) -> None:
    class _FakeRedis:
        @staticmethod
        def from_url(*_args: object, **_kwargs: object) -> object:
            raise OSError("name resolution failed")

    monkeypatch.setattr("core.readiness.Redis", _FakeRedis)

    assert await check_redis_status() == "degraded"


async def test_check_redis_status_reuses_client_across_calls(monkeypatch) -> None:
    """The High finding from the #442 security review: no per-call reconnect.

    Two calls in the same running loop must hit `Redis.from_url` once, not
    twice - the whole point of caching per loop rather than opening and
    closing a connection on every `/health/ready` poll.
    """
    fake_redis, client = _fake_redis(ping_result=True)
    monkeypatch.setattr("core.readiness.Redis", fake_redis)

    assert await check_redis_status() == "ok"
    assert await check_redis_status() == "ok"

    assert fake_redis.from_url_calls == 1
    assert client.ping_calls == 2


async def test_check_redis_status_still_degrades_after_reused_client_fails(
    monkeypatch,
) -> None:
    """A cached client that starts failing degrades on every subsequent call.

    Guards against a fix for the reconnect-cost finding accidentally caching
    the *result* instead of the *client* - degraded must not stick once
    Redis recovers, and this test's fake never recovers, so it must keep
    reporting degraded rather than freezing on the first answer.
    """
    fake_redis, client = _fake_redis(ping_result=RedisError("connection refused"))
    monkeypatch.setattr("core.readiness.Redis", fake_redis)

    assert await check_redis_status() == "degraded"
    assert await check_redis_status() == "degraded"

    assert fake_redis.from_url_calls == 1
    assert client.ping_calls == 2


# ---------------------------------------------------------------------------
# check_schema_readiness — injected sources, no DB.
# ---------------------------------------------------------------------------


async def _db_returns(values: tuple[str, ...]):
    async def _fn(_session: object) -> tuple[str, ...]:
        return values

    return _fn


async def test_check_ready_when_db_matches_heads() -> None:
    result = await check_schema_readiness(
        session=None,  # unused — db source is injected
        expected_heads=lambda: ("0021_head",),
        db_revisions=await _db_returns(("0021_head",)),
    )
    assert result.ready is True
    assert result.expected == ("0021_head",)
    assert result.current == ("0021_head",)
    assert result.error is None


async def test_check_not_ready_when_db_behind_head() -> None:
    result = await check_schema_readiness(
        session=None,
        expected_heads=lambda: ("0021_head",),
        db_revisions=await _db_returns(("0020_prev",)),
    )
    assert result.ready is False
    # summary names both revisions, no sensitive material.
    summary = result.summary()
    assert "0021_head" in summary
    assert "0020_prev" in summary


async def test_check_not_ready_when_alembic_version_empty() -> None:
    result = await check_schema_readiness(
        session=None,
        expected_heads=lambda: ("0021_head",),
        db_revisions=await _db_returns(()),
    )
    assert result.ready is False
    assert result.current == ()


async def test_check_not_ready_when_db_source_raises() -> None:
    """A missing alembic_version table / unreachable DB → not ready, terse reason.

    The raw exception text must NOT leak into the result (security): we assert the
    error reason is the generic, pre-masked string, not the raised message.
    """

    async def _boom(_session: object) -> tuple[str, ...]:
        raise RuntimeError("relation \"alembic_version\" does not exist @ host=secret")

    result = await check_schema_readiness(
        session=None,
        expected_heads=lambda: ("0021_head",),
        db_revisions=_boom,
    )
    assert result.ready is False
    assert result.error is not None
    assert "secret" not in result.error
    assert "host=" not in result.error
    # summary() returns the (non-sensitive) error reason, not the raw exception.
    assert result.summary() == result.error


async def test_check_not_ready_when_expected_heads_raises() -> None:
    """A broken Alembic script tree → not ready, generic reason, no 500.

    Exercises the defensive guard around the expected-heads computation: any
    failure there must fold into a 503-able not-ready result, never bubble.
    """

    def _boom_heads() -> tuple[str, ...]:
        raise RuntimeError("cannot load script directory")

    result = await check_schema_readiness(
        session=None,
        expected_heads=_boom_heads,
        db_revisions=await _db_returns(("0021_head",)),
    )
    assert result.ready is False
    assert result.error is not None
    assert "cannot load script directory" not in result.error


async def test_check_not_ready_when_heads_empty() -> None:
    """An empty expected-head set (degenerate) is never 'ready'."""
    result = await check_schema_readiness(
        session=None,
        expected_heads=lambda: (),
        db_revisions=await _db_returns(()),
    )
    assert result.ready is False


# ---------------------------------------------------------------------------
# Route contract — GET /health/ready via ASGI client.
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    from main import app as fastapi_app

    return fastapi_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


async def test_route_ready_returns_200(monkeypatch, app, client) -> None:
    async def _ok(_session, **_kw):
        return ReadinessResult(ready=True, expected=("h",), current=("h",))

    # Patch the symbol imported into the health route module.
    import api.v1.health as health_mod

    monkeypatch.setattr(health_mod, "check_schema_readiness", _ok)
    monkeypatch.setattr(health_mod, "check_redis_status", _returns("ok"))

    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "redis": "ok"}


async def test_route_not_ready_returns_503_problem_json(monkeypatch, app, client) -> None:
    async def _behind(_session, **_kw):
        return ReadinessResult(ready=False, expected=("0021",), current=("0020",))

    import api.v1.health as health_mod

    monkeypatch.setattr(health_mod, "check_schema_readiness", _behind)
    monkeypatch.setattr(health_mod, "check_redis_status", _returns("ok"))

    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    for key in ("type", "title", "status", "detail", "instance"):
        assert key in body, f"problem response missing required field: {key}"
    assert body["status"] == 503
    assert body["instance"] == "/health/ready"
    assert body["ready"] is False
    # detail summarises the mismatch with short revision ids only.
    assert "0021" in body["detail"]
    assert "0020" in body["detail"]


async def test_route_is_unauthenticated(monkeypatch, app, client) -> None:
    """The probe must answer with NO Authorization header (CLAUDE.md #12 public)."""

    async def _ok(_session, **_kw):
        return ReadinessResult(ready=True, expected=("h",), current=("h",))

    import api.v1.health as health_mod

    monkeypatch.setattr(health_mod, "check_schema_readiness", _ok)
    monkeypatch.setattr(health_mod, "check_redis_status", _returns("ok"))

    # No auth header supplied at all.
    resp = await client.get("/health/ready")
    assert resp.status_code == 200


def _returns(value: str):
    async def _fn() -> str:
        return value

    return _fn


async def test_route_schema_ready_but_redis_degraded_stays_200(monkeypatch, app, client) -> None:
    """A Redis outage is reported, not enforced (issue #399): status stays 200."""

    async def _ok(_session, **_kw):
        return ReadinessResult(ready=True, expected=("h",), current=("h",))

    import api.v1.health as health_mod

    monkeypatch.setattr(health_mod, "check_schema_readiness", _ok)
    monkeypatch.setattr(health_mod, "check_redis_status", _returns("degraded"))

    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "redis": "degraded"}


async def test_route_not_ready_carries_redis_field_too(monkeypatch, app, client) -> None:
    async def _behind(_session, **_kw):
        return ReadinessResult(ready=False, expected=("0021",), current=("0020",))

    import api.v1.health as health_mod

    monkeypatch.setattr(health_mod, "check_schema_readiness", _behind)
    monkeypatch.setattr(health_mod, "check_redis_status", _returns("degraded"))

    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["redis"] == "degraded"
