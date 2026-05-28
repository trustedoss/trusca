"""
Rate-limiting configuration — slowapi.

Phase 1 PR #5 — task 1.5.

CLAUDE.md §3 baseline: 5 login attempts per minute per IP. The default
per-route policy is empty so we only apply limits where explicitly decorated.

The 429 handler emits an RFC 7807 problem+json body with a `Retry-After`
header. Unit tests call the handler directly to assert the contract.

H-4 (security-reviewer blocker):
  - The key function must honour `X-Forwarded-For` so reverse proxies in
    front of FastAPI (Traefik, nginx, GCP LB) report the real client IP
    instead of the proxy's loopback. Without this every caller shares the
    same bucket and the limiter becomes a global rate cap.
  - Storage is Redis (shared across uvicorn workers / Celery beats), not
    slowapi's default in-memory dict, so the 5/min budget is enforced
    correctly under multi-worker deployments.
"""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from core.config import redis_url
from core.errors import PROBLEM_CONTENT_TYPE

# Module-level constant — this is policy, not configuration. Keep it in code.
LOGIN_RATE_LIMIT = "5/minute"


def _limiter_enabled() -> bool:
    """
    PR #9 e2e fix: GitHub-hosted runners share a single egress IP, so the
    5/min login budget is consumed across the auth.spec.ts (3 logins) +
    scan_flow.spec.ts (4 logins + retries) in a single e2e job and trips
    "Rate limit exceeded" on the third / fourth scenario. Setting
    RATELIMIT_DISABLED=1 in the e2e workflow disables slowapi for that job
    only. Production / dev keep the variable unset → enabled by default.

    The toggle is environment-only: there is no API path or admin surface
    to flip it at runtime, which keeps the production posture safe.
    """
    return os.getenv("RATELIMIT_DISABLED", "").lower() not in ("1", "true", "yes")


def _client_ip_for_limit(request: Request) -> str:
    """
    H-4: prefer the leftmost X-Forwarded-For entry (reverse proxies set this
    to the real client IP), fall back to the ASGI client tuple via slowapi's
    `get_remote_address`. Mirrors `core.middleware._extract_client_ip` so
    audit + rate-limit see the same IP for any given request.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        first = fwd.split(",", 1)[0].strip()
        if first:
            return first
    return get_remote_address(request)


def _authenticated_user_key(request: Request) -> str:
    """slowapi key function for per-*user* limits on authenticated endpoints.

    B1: scan triggers should be throttled per user, not per IP — many
    legitimate users (and CI runners) share an egress IP behind NAT, so an
    IP-keyed bucket would let one abuser starve everyone or, conversely,
    punish a whole office for one user's burst.

    We derive the key from the access token's ``sub`` claim. The slowapi key
    function runs in the route decorator *before* FastAPI resolves the
    ``current_user`` dependency, so we decode the bearer token directly here
    (signature + type + expiry verified). Resolution order:

      1. ``user:<uuid>``  — valid access token present (the normal path; the
         route requires auth so this is what we expect).
      2. ``ip:<addr>``    — no/invalid/expired token. The auth dependency will
         reject the request with 401 regardless; bucketing by IP in this case
         just prevents an unauthenticated flood from sharing the global
         no-key bucket.

    Decoding is cheap (HS256 verify) and the token is already in memory; we
    deliberately do NOT hit the DB here (the key func is on the hot path and
    must not add a round-trip).
    """
    # Local imports keep this module importable without pulling the security
    # stack at load time and avoid a circular import (security -> audit ->
    # config; ratelimit -> security would close the loop).
    from jose import JWTError

    from core.security import TOKEN_TYPE_ACCESS, _bearer_token, decode_token

    token = _bearer_token(request)
    if token:
        try:
            claims = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
            sub = claims.get("sub")
            if sub:
                return f"user:{sub}"
        except (JWTError, ValueError):
            pass
    return f"ip:{_client_ip_for_limit(request)}"


# Limiter keyed by client IP. `default_limits=[]` → endpoints opt in via
# @limiter.limit(...). storage_uri uses Redis so the 5/min budget is shared
# across uvicorn workers (the function call is a runtime call, not a cached
# module constant — CLAUDE.md rule #11 is about getenv, not bootstrap config).
limiter = Limiter(
    key_func=_client_ip_for_limit,
    default_limits=[],
    storage_uri=redis_url(),
    enabled=_limiter_enabled(),
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Convert slowapi's RateLimitExceeded into RFC 7807 + Retry-After.

    Retry-After is a fixed 60s for the login limiter; if we add more granular
    policies later we can derive it from `exc.limit.error_message`.
    """
    detail = getattr(getattr(exc, "limit", None), "error_message", None) or "Rate limit exceeded"
    body = {
        "type": "about:blank",
        "title": "Too Many Requests",
        "status": 429,
        "detail": str(detail),
        "instance": getattr(getattr(request, "url", None), "path", None) or "/",
    }
    response = JSONResponse(
        body,
        status_code=429,
        media_type=PROBLEM_CONTENT_TYPE,
    )
    response.headers["Retry-After"] = "60"
    return response
