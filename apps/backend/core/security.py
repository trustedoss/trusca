# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Authentication primitives — password hashing, JWT mint/verify, RBAC.

Phase 1 PR #5 — task 1.2 + 1.3.

Design choices:
  - We implement JWT directly with python-jose rather than pulling fastapi-users
    in. The user model is fastapi-users compatible (is_active/is_superuser/
    is_verified), so a future migration is trivial; until then, direct
    implementation keeps the surface small and the unit tests fast.
  - Refresh tokens are full JWTs with a separate `type` claim and a `jti`
    (uuid4 hex) so we can store revocation state by jti in `refresh_tokens`.
  - `decode_token(expected_type=...)` enforces type isolation: an access token
    cannot be replayed against /auth/refresh and vice versa.
  - bcrypt cost 12 per CLAUDE.md §3.

RBAC:
  - `get_current_user` parses the Authorization header, verifies the access
    token, loads the user + memberships from Postgres, and returns a
    `CurrentUser` (dataclass).
  - `require_role(role)` returns a dependency that resolves to the current
    user when their role meets or exceeds the demanded role; raises
    HTTPException(401) for anonymous and 403 for insufficient privilege.
  - `require_team_member()` returns a dependency that resolves a `team_id`
    path/query param against `current_user.team_ids`; super_admin bypasses.

The dependency factories return plain callables that the unit tests can call
directly with kwargs (`dep(current_user=user)`, `dep(team_id=t, current_user=u)`).
FastAPI is happy to inject the same callable via `Depends(...)` in route
signatures because the parameter names match dependency names.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.audit import audit_context
from core.config import (
    access_token_expire_minutes,
    permission_cache_ttl_seconds,
    refresh_token_expire_days,
    secret_key,
)
from core.db import get_db

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

# Role priority. Higher value means more privileged.
#
# The numbers start at 1, not 0, because ``_has_at_least`` reads this map with
# ``.get(role, 0)``: an unknown role compares as 0 and is denied everywhere,
# which is the safe direction for a typo. A real grade sitting at 0 would be
# indistinguishable from that, so ``viewer`` takes 1 and the grades above it
# each moved up by one. Comparisons are relative, so nothing else changes.
_ROLE_PRIORITY: dict[str, int] = {
    "viewer": 1,
    "developer": 2,
    "team_admin": 3,
    "super_admin": 4,
}

# Bcrypt cost is fixed at 12 (CLAUDE.md §3 security default).
_pwd_context = CryptContext(schemes=["bcrypt"], bcrypt__rounds=12, deprecated="auto")

log = structlog.get_logger("auth")


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    """Return a bcrypt hash with cost 12. Truncates to 72 bytes per bcrypt."""
    # bcrypt has a 72-byte hard limit on the input. passlib raises for longer
    # inputs unless explicitly truncated; we trim defensively because users
    # may paste a long passphrase. The truncation is documented in the
    # registration validation path.
    return str(_pwd_context.hash(plain[:72]))


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt verification. Returns False on any error."""
    try:
        return bool(_pwd_context.verify(plain[:72], hashed))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def create_access_token(
    *,
    subject: str,
    role: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint an access JWT. TTL from `ACCESS_TOKEN_EXPIRE_MINUTES`."""
    now = _now()
    expires = now + timedelta(minutes=access_token_expire_minutes())
    claims: dict[str, Any] = {
        "sub": subject,
        "type": TOKEN_TYPE_ACCESS,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if role is not None:
        claims["role"] = role
    if extra_claims:
        claims.update(extra_claims)
    return str(jwt.encode(claims, secret_key(), algorithm=JWT_ALGORITHM))


def create_refresh_token(
    *,
    subject: str,
    parent_jti: str | None = None,
) -> tuple[str, str, datetime]:
    """
    Mint a refresh JWT.

    Returns (token, jti, expires_at) so the caller can persist the row in
    `refresh_tokens` and set the cookie atomically.
    """
    now = _now()
    expires = now + timedelta(days=refresh_token_expire_days())
    jti = uuid.uuid4().hex
    claims: dict[str, Any] = {
        "sub": subject,
        "type": TOKEN_TYPE_REFRESH,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": jti,
    }
    if parent_jti is not None:
        claims["parent_jti"] = parent_jti
    token = str(jwt.encode(claims, secret_key(), algorithm=JWT_ALGORITHM))
    return token, jti, expires


def decode_token(token: str, *, expected_type: str) -> dict[str, Any]:
    """
    Verify signature + expiration + type. Raise on any mismatch.

    Callers should catch JWTError or ValueError and translate into 401.
    """
    claims: dict[str, Any] = jwt.decode(token, secret_key(), algorithms=[JWT_ALGORITHM])
    actual_type = claims.get("type")
    if actual_type != expected_type:
        raise JWTError(f"unexpected token type: {actual_type!r} != {expected_type!r}")
    return claims


def hash_refresh_token(token: str) -> str:
    """sha256 hex digest used in `refresh_tokens.token_hash`."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CurrentUser + RBAC
# ---------------------------------------------------------------------------


@dataclass
class CurrentUser:
    """Light-weight authenticated principal for dependency injection.

    `role` is the *highest* role across the user's memberships and is used by
    coarse, route-level gates such as `require_role(...)`. It must NOT be used
    for per-team authorization decisions: a user who is `team_admin` in team_a
    and `developer` in team_b would otherwise pass write checks against team_b
    projects (cross-team role escalation — OWASP A01:2021 / CWE-863).

    `team_roles` is the per-team mapping of `team_id -> role` and is what
    service-layer write checks (`_can_write_project`, etc.) consult to make
    sure the actor's role is evaluated against the *project's* team, never
    against an unrelated team where the actor happens to be more privileged.
    """

    id: uuid.UUID
    email: str
    role: str  # highest role across memberships (super_admin > team_admin > developer)
    team_ids: list[uuid.UUID] = field(default_factory=list)
    team_roles: dict[uuid.UUID, str] = field(default_factory=dict)
    is_active: bool = True
    is_superuser: bool = False
    # M-2: set ONLY when the principal was synthesized from a project-scoped
    # API key (``core.api_key_auth``). Narrowing to the project's team is not
    # enough — a single-project CI key must not trigger scans on the team's
    # OTHER projects — so project-touching authorization gates compare this
    # against the target project id. ``None`` for JWT principals and for
    # team / org-scoped keys (no project boundary applies).
    api_key_project_id: uuid.UUID | None = None
    # N7: set ONLY when the principal was synthesized from a read-only API key.
    # Enforced by HTTP method in ``core.api_key_auth._assert_breadth_allows``,
    # which runs as the principal is built rather than at any one route gate.
    # Two surfaces resolve a key through their own dependency instead of the
    # shared gate, so enforcing there left a POST reachable; every dispatcher
    # inherits it here. False for every JWT principal: a person's session is
    # not narrowed by this.
    api_key_read_only: bool = False


def _highest_role(roles: list[str], *, is_superuser: bool) -> str:
    if is_superuser:
        return "super_admin"
    if not roles:
        return "developer"
    return max(roles, key=lambda r: _ROLE_PRIORITY.get(r, 0))


def _has_at_least(actual: str, required: str) -> bool:
    return _ROLE_PRIORITY.get(actual, 0) >= _ROLE_PRIORITY.get(required, 0)


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


# ---------------------------------------------------------------------------
# Reusing a resolved principal (N5)
#
# Off unless a deployment sets a lifetime. What is cached is the answer to
# "who is this and what may they do", which two queries rebuild on every
# authenticated request; what makes caching it a decision rather than an
# optimisation is that a stale answer keeps a demoted person at their old
# grade. The lifetime is therefore the whole contract, and it is an upper
# bound on how long a revocation can go unfelt.
#
# In the process rather than in Redis. A shared cache would need its own
# invalidation protocol, a second failure mode on the authentication path, and
# a round trip that eats much of what it saves. Per-process means each worker
# holds its own copy, and the guarantee is the lifetime: nothing older than it
# is used, anywhere.
#
# Writes that change somebody's standing drop their entry, but only in the
# process that handled the write, and the shipped deployments run four workers
# (docker-compose) or two pods of four (Helm). So the drop is a small
# optimisation, not a second guarantee: on a normal deployment most requests
# after a demotion still land on a worker holding the old answer until the
# lifetime expires. An operator who needs a revocation to be immediate leaves
# the lifetime at zero, and the documentation says so rather than implying the
# drop covers them.
# ---------------------------------------------------------------------------

#: user id -> (principal, monotonic deadline).
_principal_cache: dict[uuid.UUID, tuple[CurrentUser, float]] = {}

#: How many principals one process will hold.
#:
#: An expired entry is only noticed when somebody looks it up, so a deployment
#: where ten thousand people each sign in once would keep ten thousand dead
#: entries that nothing ever reads again. The bound is what makes this a cache
#: rather than a slowly filling map; the number is generous because the thing
#: held is small and the deployments that turn this on are the ones with a lot
#: of people.
_PRINCIPAL_CACHE_MAX_ENTRIES = 10_000


def _cached_principal(user_id: uuid.UUID, ttl: int) -> CurrentUser | None:
    """The stored principal when it is still inside its lifetime.

    Reads the clock through ``time.monotonic``, which cannot go backwards. A
    wall clock that a system daemon steps forward would expire entries early
    (harmless) and one stepped back would hold them past their lifetime, which
    is the direction that breaks the promise this makes.
    """
    if ttl <= 0:
        return None
    entry = _principal_cache.get(user_id)
    if entry is None:
        return None
    principal, deadline = entry
    if time.monotonic() >= deadline:
        del _principal_cache[user_id]
        return None
    return _detached(principal)


def _remember_principal(principal: CurrentUser, ttl: int) -> None:
    if ttl <= 0:
        return
    # A key principal carries the issuer's user id, so it would be stored
    # under the same key their browser session uses. Serving one to the other
    # would hand a scoped CI key the issuer's whole membership set, or hand
    # their session a read-only flag. Only the JWT path reaches this function
    # today; the refusal is here so that stays true without depending on who
    # calls it.
    if principal.api_key_project_id is not None or principal.api_key_read_only:
        return
    if (
        principal.id not in _principal_cache
        and len(_principal_cache) >= _PRINCIPAL_CACHE_MAX_ENTRIES
    ):
        # Drop the oldest rather than scanning for the soonest to expire. A
        # dict keeps insertion order and every entry in a process is written
        # with the same lifetime, so the oldest is the soonest anyway, and the
        # scan it replaces ran on the event loop once per insert at exactly
        # the load this feature exists for.
        del _principal_cache[next(iter(_principal_cache))]
    _principal_cache[principal.id] = (_detached(principal), time.monotonic() + ttl)


def _detached(principal: CurrentUser) -> CurrentUser:
    """A copy that shares no mutable state with the caller's.

    The dataclass holds a list and a dict, and the stored object outlives the
    request that built it. Without this, one ``actor.team_roles[t] = ...``
    added anywhere downstream would rewrite what everybody holding that
    session may do, for the rest of the lifetime. The key-principal path next
    door already copies for the same reason, on the path where a mutation
    would at least die with the request.
    """
    return replace(
        principal,
        team_ids=list(principal.team_ids),
        team_roles=dict(principal.team_roles),
    )


def forget_principal(user_id: uuid.UUID) -> None:
    """Drop a cached principal after something changed what they may do.

    Called from the writes that change a grade or an activation. It is not the
    guarantee, which is the lifetime; it is what makes the ordinary case
    immediate, so an administrator who demotes somebody and refreshes the
    screen sees the new grade rather than waiting out a timer.

    Safe to call when nothing is cached, and safe to call when the lifetime is
    zero: there is nothing to drop either way.
    """
    _principal_cache.pop(user_id, None)


def reset_principal_cache() -> None:
    """Empty it. For tests, and for a process that has just changed the TTL."""
    _principal_cache.clear()


async def _load_current_user(
    request: Request,
    session: AsyncSession,
) -> CurrentUser | None:
    """
    Resolve the bearer token in the request to a CurrentUser, or None.

    Returns None for the anonymous case so dependency factories can decide
    whether to raise 401 themselves (some endpoints want optional auth).
    """
    token = _bearer_token(request)
    if not token:
        return None
    try:
        claims = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
    except (JWTError, ValueError):
        return None

    sub = claims.get("sub")
    if not sub:
        return None
    try:
        user_id = uuid.UUID(str(sub))
    except (ValueError, TypeError):
        return None

    ttl = permission_cache_ttl_seconds()
    cached = _cached_principal(user_id, ttl)
    if cached is not None:
        _bind_audit_actor(cached)
        return cached

    # Local import — avoids a circular import at module load (models -> Base
    # -> auth which references nothing from us, but keeping the import lazy
    # makes the security module safe to import from anywhere).
    from models import Membership, User

    stmt = select(User).where(User.id == user_id).options(selectinload(User.memberships))
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        return None

    memberships: list[Membership] = list(user.memberships)
    team_ids = [m.team_id for m in memberships]
    team_roles = {m.team_id: m.role for m in memberships}
    role = _highest_role(
        [m.role for m in memberships],
        is_superuser=bool(user.is_superuser),
    )

    cu = CurrentUser(
        id=user.id,
        email=user.email,
        role=role,
        team_ids=team_ids,
        team_roles=team_roles,
        is_active=bool(user.is_active),
        is_superuser=bool(user.is_superuser),
    )

    # Not cached: an inactive principal is one every caller refuses anyway,
    # and holding it would mean a reactivated person waits out a timer to get
    # back in. The refusal costs the queries; the ordinary path is what this
    # is here to save.
    if cu.is_active:
        _remember_principal(cu, ttl)

    _bind_audit_actor(cu)
    return cu


def _bind_audit_actor(cu: CurrentUser) -> None:
    """Bind the user into the audit context.

    Any flush later in this request then gets actor_user_id automatically.
    Runs on the cached path too: the audit trail is per request, and a request
    served from the cache mutates rows exactly like one that was not.
    """
    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(cu.id)
    audit_context.set(ctx)


async def get_optional_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """Dependency: returns the authenticated user or None for anonymous."""
    return await _load_current_user(request, session)


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """Dependency: 401 if missing/invalid token or inactive user."""
    user = await _load_current_user(request, session)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def require_role(role: str) -> Callable[..., CurrentUser]:
    """
    Dependency factory: ensure the caller's role meets `role`.

    Returns a callable that accepts `current_user` as a kwarg (so unit tests
    can call it directly with a mocked user) and is also FastAPI-compatible
    via the embedded `Depends(get_optional_current_user)` default.

    Role priority: super_admin > team_admin > developer.
    """

    def _check(
        current_user: CurrentUser | None = Depends(get_optional_current_user),
    ) -> CurrentUser:
        if current_user is None or not current_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        if not _has_at_least(current_user.role, role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role >= {role}",
            )
        return current_user

    return _check


def require_super_admin_or_404() -> Callable[..., CurrentUser]:
    """
    Dependency factory: super-admin gate with existence-hide behavior.

    Phase 4 PR #13 — admin endpoints. Unlike :func:`require_role`, this
    dependency hides the very existence of the admin surface from non-admin
    callers. The contract is:

      - Anonymous (no/invalid JWT)         -> 401 Authentication required
      - Authenticated but not super_admin  -> 404 Not Found
      - Authenticated super_admin          -> pass-through

    Returning 404 (not 403) for non-super-admin authed users prevents probing
    the admin URL space — `team_admin` and `developer` users get the same
    response shape as if the path didn't exist.

    The 401 branch stays distinct because surfacing "you're unauthenticated"
    is not a privacy issue (the JWT is the credential — its absence is the
    answer). Hiding 401 behind 404 would also break the standard
    "401 -> redirect to login" pattern in the frontend client.

    The dependency-shaped callable accepts ``current_user`` as a kwarg so
    unit tests can call it directly without going through FastAPI.
    """

    def _check(
        current_user: CurrentUser | None = Depends(get_optional_current_user),
    ) -> CurrentUser:
        if current_user is None or not current_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        # Existence-hide: anything below super_admin sees 404, never 403.
        # Pair `is_superuser` with `role == "super_admin"` for safety —
        # `_load_current_user` keeps them in lockstep but this is the
        # privilege-decision site, so we double-check.
        if not (current_user.is_superuser or current_user.role == "super_admin"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Not Found",
            )
        return current_user

    return _check


def require_team_member() -> Callable[..., CurrentUser]:
    """
    Dependency factory: ensure the caller belongs to `team_id`.

    Returns a callable that accepts `team_id` (UUID) and `current_user` as
    kwargs. super_admin bypasses the team check entirely; everyone else must
    have `team_id in current_user.team_ids`.
    """

    def _check(
        team_id: uuid.UUID,
        current_user: CurrentUser | None = Depends(get_optional_current_user),
    ) -> CurrentUser:
        if current_user is None or not current_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
            )
        if current_user.role == "super_admin":
            return current_user
        if team_id not in current_user.team_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not a member of this team",
            )
        return current_user

    return _check


__all__ = [
    "CurrentUser",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "get_optional_current_user",
    "hash_password",
    "hash_refresh_token",
    "require_role",
    "require_super_admin_or_404",
    "require_team_member",
    "verify_password",
]
