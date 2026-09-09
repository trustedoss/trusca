# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Rate-limiting configuration — slowapi.

Phase 1 PR #5 — task 1.5.

CLAUDE.md §3 baseline: 5 login attempts per minute per IP. The default
per-route policy is empty so we only apply limits where explicitly decorated.

The 429 handler emits an RFC 7807 problem+json body with a `Retry-After`
header. Unit tests call the handler directly to assert the contract.

H-4 (security review blocker):
  - The key function must honour `X-Forwarded-For` so reverse proxies in
    front of FastAPI (Traefik, nginx, GCP LB) report the real client IP
    instead of the proxy's loopback. Without this every caller shares the
    same bucket and the limiter becomes a global rate cap.
  - Storage is Redis (shared across uvicorn workers / Celery beats), not
    slowapi's default in-memory dict, so the 5/min budget is enforced
    correctly under multi-worker deployments.

Redis failure policy (issue #399): fail open, the same policy `login_throttle.py`
already documents for the login guesswork slowdown. A rate limit's job is to
keep one caller from crowding out everyone else; it is not the last line of
defence for anything it guards, and turning a Redis outage into a site-wide
outage would make a shared cache a hard dependency of every limited endpoint,
login included, for the sake of a control that is explicitly a courtesy
rather than a wall.

That policy is not the one slowapi ships with. Left to its defaults,
`Limiter` re-raises whatever its storage raises: `RateLimitExceeded` first
became a real question during this issue when a Redis restart in dev turned
every limited endpoint into a 500 instead of the plain pass-through the name
"rate limit" implies. `swallow_errors=True` does not fix it either -- slowapi
only sets `request.state.view_rate_limit` once its evaluation loop finishes
without raising, and the header-injection code that runs after the swallowed
exception reads that attribute unconditionally, so the endpoint still 500s
with an `AttributeError` instead of a `RedisError`. Confirmed by driving both
configurations against an unreachable Redis directly (see
`tests/unit/test_rate_limit.py`); neither is a shape this module can rely on.

So the fix sits one layer down, in the storage `FailOpenRedisStorage`
plugs in below. `Limiter.hit()` only ever asks its storage two things:
"how many hits does this key have" (`incr`, `get`) and "when does it reset"
(`get_expiry`). Answering "zero" and "now" for those during an outage is
indistinguishable, from the strategy's point of view, from a key that has
never been hit -- the request is allowed, the evaluation loop finishes
normally, and `request.state.view_rate_limit` gets set exactly like the
healthy path. Nothing upstream needs to know Redis was unreachable; slowapi
only sees answers it already knows how to handle.
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from limits.storage.redis import RedisStorage
from redis.exceptions import RedisError
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from core import redis_degradation
from core.config import redis_url
from core.errors import PROBLEM_CONTENT_TYPE

log = structlog.get_logger("ratelimit")

#: A blocked call here sits in front of every limited endpoint, so it gets the
#: same short leash `login_throttle.py` gives its own Redis client: without a
#: bound, a Redis that drops packets rather than refusing them hangs the
#: request instead of failing it, which defeats the point of failing open.
_SOCKET_TIMEOUT_SECONDS = 1.0

#: `redis://` and `rediss://` rewritten to schemes `FailOpenRedisStorage`
#: registers itself under, so `Limiter(storage_uri=...)` picks our subclass
#: instead of `limits`' own `RedisStorage`. `redis+unix://` is intentionally
#: left alone: nothing in this deployment uses it, and guessing at a
#: transform for a scheme nobody exercises is worse than leaving it on the
#: standard (fail-closed) storage and finding out.
_FAIL_OPEN_SCHEMES = {"redis": "redis+failopen", "rediss": "rediss+failopen"}


def _fail_open_storage_uri(url: str) -> str:
    """Rewrite a `redis(s)://` URL to the scheme `FailOpenRedisStorage` owns."""
    scheme, sep, rest = url.partition("://")
    mapped = _FAIL_OPEN_SCHEMES.get(scheme)
    if not sep or mapped is None:
        return url
    return f"{mapped}{sep}{rest}"


def _degraded(action: str, exc: Exception) -> None:
    """Redis could not answer a rate-limit check. Say so, then allow it.

    Mirrors `login_throttle._degraded`: the warning exists so an outage is
    visible to somebody reading logs, since the outward behaviour is the same
    limiter quietly not counting anything until Redis answers again.

    Routed through `core.redis_degradation` (#419/#420) rather than logging
    directly: it dedupes this WARNING under sustained failures instead of
    one line per request, and keeps a count + last-degraded timestamp
    `/health/ready` can surface even between individual failures.
    """
    redis_degradation.record(
        component="ratelimit",
        event="ratelimit.storage_unavailable",
        action=action,
        exc=exc,
    )


class FailOpenRedisStorage(RedisStorage):
    """`limits`' Redis storage, answering "not limited" when Redis cannot answer.

    Registers itself under `redis+failopen` / `rediss+failopen` (see
    `STORAGE_SCHEME` below) rather than overriding `redis` / `rediss`
    directly, so a reader who greps for the scheme in a stack trace finds
    this class instead of wondering why `limits`' own `RedisStorage` grew a
    fail-open habit it does not document.

    Overrides only the three calls `FixedWindowRateLimiter` (this app's
    strategy; see the `Limiter(...)` construction below) makes: `incr`,
    `get`, `get_expiry`. `check()` already reports "unhealthy" rather than
    raising in the parent class, and `reset()` / `clear()` are operator
    tools this app does not call from the request path, so a failure there
    is somebody's terminal, not somebody's login.
    """

    STORAGE_SCHEME = ["redis+failopen", "rediss+failopen"]

    def __init__(self, uri: str, **options: Any) -> None:
        scheme, sep, rest = uri.partition("://")
        real_scheme = scheme.removesuffix("+failopen")
        super().__init__(f"{real_scheme}{sep}{rest}", **options)

    def incr(self, key: str, expiry: int, amount: int = 1) -> int:
        """Zero hits recorded reads as "not limited" to every caller of this."""
        try:
            return super().incr(key, expiry, amount=amount)
        except RedisError as exc:
            _degraded("incr", exc)
            return 0

    def get(self, key: str) -> int:
        try:
            return super().get(key)
        except RedisError as exc:
            _degraded("get", exc)
            return 0

    def get_expiry(self, key: str) -> float:
        """A key we could not read reports its expiry as now.

        We do not know when it actually resets, and claiming a real deadline
        would tell a caller building on `X-RateLimit-Reset` something we did
        not observe.
        """
        try:
            return super().get_expiry(key)
        except RedisError as exc:
            _degraded("get_expiry", exc)
            return time.time()


# Module-level constant — this is policy, not configuration. Keep it in code.
LOGIN_RATE_LIMIT = "5/minute"

# Starting an OAuth sign-in is not a credential guess, so it does not take the
# login budget. It gets its own, looser one: the endpoint is shared by everyone
# behind an office NAT, and single sign-on means the whole office arrives
# within the same few minutes. The reason to limit it at all is that the
# generic provider resolves its endpoints from the issuer, so a cold cache
# costs an outbound request; the size of that cost is bounded by the discovery
# failure cache rather than by this number.
OAUTH_AUTHORIZE_RATE_LIMIT = "60/minute"


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
    from services.api_key_service import parse_bearer

    token = _bearer_token(request)
    if token:
        # CI scan-action authenticates with a tos_ API key, and the scan-trigger
        # endpoint now accepts it. Bucket those by key prefix (cheap — no bcrypt,
        # no DB) so many keys sharing one CI egress IP don't collapse into a
        # single ip: bucket (security review, low severity). parse_bearer returns the
        # 12-char prefix without verifying the secret — fine for a rate-limit
        # key (a forged prefix only shares a bucket with itself).
        parsed = parse_bearer(token)
        if parsed is not None:
            return f"apikey:{parsed[0]}"
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
# The `+failopen` scheme routes construction to `FailOpenRedisStorage` above
# instead of `limits`' own `RedisStorage`; the socket timeouts bound how long
# a hung connection can delay a request before that fail-open policy applies.
limiter = Limiter(
    key_func=_client_ip_for_limit,
    default_limits=[],
    storage_uri=_fail_open_storage_uri(redis_url()),
    # slowapi types storage_options as Dict[str, str], but it forwards the
    # values unchanged to redis.from_url(**options), which wants real floats
    # here rather than strings it would have to parse back.
    storage_options={
        "socket_timeout": _SOCKET_TIMEOUT_SECONDS,  # type: ignore[dict-item]
        "socket_connect_timeout": _SOCKET_TIMEOUT_SECONDS,  # type: ignore[dict-item]
    },
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
