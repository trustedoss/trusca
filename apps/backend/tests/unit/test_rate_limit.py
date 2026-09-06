"""
Unit tests for the slowapi rate-limit configuration.

We don't go through HTTP here; we just check that the limiter object is
configured per CLAUDE.md §3 (5 requests/min on /auth/login, IP-keyed) and
that the 429 handler returns RFC 7807 + Retry-After.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address


def test_login_rate_limit_is_five_per_minute():
    from core.ratelimit import LOGIN_RATE_LIMIT

    assert LOGIN_RATE_LIMIT == "5/minute"


def test_registration_rate_limit_defaults_and_can_be_overridden(monkeypatch):
    from core.config import registration_rate_limit

    assert registration_rate_limit() == "5/minute"

    monkeypatch.setenv("REGISTRATION_RATE_LIMIT", "2/minute")
    assert registration_rate_limit() == "2/minute"


def test_rate_limit_handler_emits_problem_response():
    from unittest.mock import MagicMock

    from slowapi.errors import RateLimitExceeded

    from core.ratelimit import rate_limit_exceeded_handler

    request = MagicMock()
    request.url.path = "/auth/login"
    request.headers = {}

    limit = MagicMock()
    limit.error_message = "5 per 1 minute"

    exc = RateLimitExceeded(limit)
    response = rate_limit_exceeded_handler(request, exc)

    assert response.status_code == 429
    assert response.media_type == "application/problem+json"
    assert "Retry-After" in response.headers


def test_limiter_keys_by_ip():
    """The limiter must key by the caller's IP via our XFF-aware helper.

    H-4: we replaced slowapi's default `get_remote_address` with
    `_client_ip_for_limit` so reverse proxies in front of FastAPI bucket the
    correct origin IP rather than the proxy's loopback.
    """
    from core.ratelimit import _client_ip_for_limit, limiter

    assert limiter._key_func is _client_ip_for_limit  # type: ignore[attr-defined]


def test_client_ip_helper_prefers_x_forwarded_for():
    """First entry of X-Forwarded-For is the real client behind a proxy."""
    from unittest.mock import MagicMock

    from core.ratelimit import _client_ip_for_limit

    req = MagicMock()
    req.headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
    assert _client_ip_for_limit(req) == "203.0.113.7"


def test_client_ip_helper_falls_back_to_remote_address():
    """No XFF header → fall through to slowapi's socket-based extractor."""
    from unittest.mock import MagicMock

    from core.ratelimit import _client_ip_for_limit

    req = MagicMock()
    req.headers = {}
    req.client.host = "198.51.100.42"
    # Starlette's get_remote_address reads request.client.host
    assert _client_ip_for_limit(req) == "198.51.100.42"


# ---------------------------------------------------------------------------
# Redis failure policy (#399): fail open, matching login_throttle.py.
# ---------------------------------------------------------------------------


def test_the_live_limiter_is_wired_to_fail_open_storage():
    """The module singleton, not just the class, has to use it.

    `FailOpenRedisStorage` existing is not the same claim as the app's one
    `limiter` actually being built with it. slowapi keeps two references to
    the storage instance (`Limiter._storage` and `Limiter._limiter.storage`,
    the second is what `hit()` actually calls) and it is easy to fix one
    while leaving the other pointed at plain `RedisStorage`.
    """
    from core.ratelimit import FailOpenRedisStorage, limiter

    assert isinstance(limiter._storage, FailOpenRedisStorage)  # type: ignore[attr-defined]
    assert isinstance(limiter._limiter.storage, FailOpenRedisStorage)  # type: ignore[attr-defined]


def test_fail_open_storage_uri_rewrites_redis_schemes():
    from core.ratelimit import _fail_open_storage_uri

    assert _fail_open_storage_uri("redis://redis:6379/0") == "redis+failopen://redis:6379/0"
    assert _fail_open_storage_uri("rediss://redis:6379/0") == "rediss+failopen://redis:6379/0"
    # Nothing in this deployment uses redis+unix://; left on the standard
    # (fail-closed) storage rather than guessed at.
    assert _fail_open_storage_uri("redis+unix:///tmp/redis.sock") == "redis+unix:///tmp/redis.sock"
    assert _fail_open_storage_uri("memory://") == "memory://"


def test_fail_open_storage_answers_not_limited_when_redis_is_unreachable():
    """A real connection failure, not a mocked one.

    `RedisError` covers several concrete exception classes (connection
    refused, timeout, ...) and a mock can only stand in for the one the
    author remembered. Port 1 is not Redis and nothing will ever listen
    there, so this is a genuine, fast (sub-second, bounded by the socket
    timeouts below) connection failure.
    """
    import time

    from core.ratelimit import FailOpenRedisStorage

    storage = FailOpenRedisStorage(
        "redis+failopen://127.0.0.1:1/0",
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
    )

    assert storage.incr("some-key", 60) == 0
    assert storage.get("some-key") == 0
    assert abs(storage.get_expiry("some-key") - time.time()) < 5


def test_fail_open_storage_still_enforces_the_limit_when_redis_answers(monkeypatch):
    """The fail-open path must not swallow errors that never happened.

    Only `RedisError` degrades to "not limited"; a healthy call still
    increments and still compares against the real count.
    """
    from limits.storage.redis import RedisStorage

    from core.ratelimit import FailOpenRedisStorage

    monkeypatch.setattr(RedisStorage, "incr", lambda self, key, expiry, amount=1: 7)
    monkeypatch.setattr(RedisStorage, "get", lambda self, key: 7)

    storage = FailOpenRedisStorage(
        "redis+failopen://127.0.0.1:1/0",
        socket_timeout=0.5,
        socket_connect_timeout=0.5,
    )
    assert storage.incr("some-key", 60) == 7
    assert storage.get("some-key") == 7


def test_a_rate_limited_endpoint_survives_an_outage_end_to_end():
    """The same proof as the storage-level tests above, but through HTTP.

    Builds a throwaway app using our real `Limiter(storage_uri=...)`
    construction (the `+failopen` scheme, the socket timeouts, all of it) and
    drives it against a connection nothing answers. Before `FailOpenRedisStorage`
    this returned 500: `Limiter` re-raised the `RedisError`, and even
    `swallow_errors=True` did not save it, since the header-injection code
    that runs after a swallowed exception reads `request.state.view_rate_limit`
    unconditionally and that attribute is only set when evaluation finishes
    without raising -- confirmed by driving both configurations against this
    same unreachable address while writing this fix.
    """
    from core.ratelimit import _SOCKET_TIMEOUT_SECONDS, _fail_open_storage_uri

    test_limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],
        storage_uri=_fail_open_storage_uri("redis://127.0.0.1:1/0"),
        storage_options={
            "socket_timeout": _SOCKET_TIMEOUT_SECONDS,
            "socket_connect_timeout": _SOCKET_TIMEOUT_SECONDS,
        },
    )

    app = FastAPI()
    app.state.limiter = test_limiter

    @app.get("/ping")
    @test_limiter.limit("5/minute")
    def ping(request: Request):
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=True)
    response = client.get("/ping")
    assert response.status_code == 200, response.text
