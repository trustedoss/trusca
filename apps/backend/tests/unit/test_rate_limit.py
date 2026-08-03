"""
Unit tests for the slowapi rate-limit configuration.

We don't go through HTTP here; we just check that the limiter object is
configured per CLAUDE.md §3 (5 requests/min on /auth/login, IP-keyed) and
that the 429 handler returns RFC 7807 + Retry-After.
"""

from __future__ import annotations


def test_login_rate_limit_is_five_per_minute():
    from core.ratelimit import LOGIN_RATE_LIMIT

    assert LOGIN_RATE_LIMIT == "5/minute"


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
