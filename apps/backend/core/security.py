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

import asyncio
import hashlib
import hmac
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
from sqlalchemy.orm import joinedload

from core.audit import audit_context
from core.config import (
    access_token_expire_minutes,
    api_key_hmac_secret,
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


def set_password(user: Any, plain: str) -> None:
    """Rotate a user's password hash and stamp when it changed.

    The one place an existing user's password is written. Both halves belong
    together: the timestamp is what lets ``_load_current_user`` refuse an
    access token minted before the change, and a caller that assigned
    ``hashed_password`` directly would get a user who believes their old
    sessions ended when they have not. Doing it here rather than at the call
    sites means a future password-change path cannot forget the second half,
    because there is nothing to forget.

    Typed loosely on purpose: ``models`` imports from this module, so naming
    ``User`` here would be a cycle. ``test_password_change_invalidates_tokens``
    asserts every production path that rotates a hash comes through here.
    """
    user.hashed_password = hash_password(plain)
    user.password_changed_at = datetime.now(UTC)
    # The permission cache is keyed by user, not by token, so an entry warmed
    # before this call carries the OLD timestamp and would keep serving tokens
    # this change was meant to refuse. Dropping it here means the next request
    # rebuilds from the row.
    forget_principal(user.id)


def password_change_invalidates(
    issued_at: int | None, password_changed_at: datetime | None
) -> bool:
    """Whether a token minted at ``issued_at`` predates the password change.

    ``iat`` is whole seconds (RFC 7519) while the column keeps microseconds, so
    the comparison is made in seconds, and a token minted in the same second as
    the change is refused rather than kept.

    That direction was chosen the other way first, to avoid logging out the
    person who had just changed their password. There is nobody to protect:
    ``POST /auth/reset-password`` answers 204 with no tokens and no cookie, and
    the user is sent back to sign in. Meanwhile the leniency was reachable on
    purpose. ``/auth/refresh`` mints an access token with ``iat = now`` on
    every call, so somebody holding a stolen refresh cookie and polling it
    lands a token inside the change's own second most of the time, and that
    token then outlives the reset for its full lifetime. A window that helps
    only the attacker is not a trade.

    If a self-service change-password endpoint is added later, it should
    return a fresh token pair in the same response rather than depend on this
    boundary.

    ``None`` for either side means no opinion: a token carrying no ``iat``
    (``decode_token`` requires one, so this is defence in depth) and a NULL
    column, which is a user whose password has not changed since 0077.
    """
    if issued_at is None or password_changed_at is None:
        return False
    # asyncpg returns aware values for timestamptz, but a naive value would be
    # read in the process timezone and could produce a SMALLER epoch, skipping
    # refusals. Normalise rather than trust, as `auth_service` does for
    # refresh-token expiry.
    if password_changed_at.tzinfo is None:
        password_changed_at = password_changed_at.replace(tzinfo=UTC)
    changed_at_second = int(password_changed_at.timestamp())
    return issued_at <= changed_at_second


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt verification. Returns False on any error."""
    try:
        return bool(_pwd_context.verify(plain[:72], hashed))
    except (ValueError, TypeError):
        return False


async def verify_password_async(plain: str, hashed: str) -> bool:
    """Same as :func:`verify_password`, off the event loop.

    Bcrypt cost 12 measures ~213ms per call on commodity hardware
    (concurrency-scaling-plan-2026-08-22.md §1.5). Calling ``verify_password``
    directly from an ``async def`` endpoint runs that ~213ms of CPU work
    inline on the event loop, which stalls every other request this worker
    process is serving for the duration: one API-key or password-login
    request pauses the whole process (§1.3, unit A1). ``asyncio.to_thread``
    moves the call to the default thread pool, following the same pattern
    already used for other blocking calls in a request path (e.g.
    ``services.scan_service.check_disk_guard``).

    Callers that authenticate a real request (password login, API-key
    verification, including the timing-flattening dummy-hash branch) should
    use this wrapper. Callers outside the request/response cycle (scripts,
    the password-reset token sweep, and unit tests exercising the primitive
    directly) may keep calling the synchronous :func:`verify_password`,
    since there is no event loop for them to block.
    """
    return await asyncio.to_thread(verify_password, plain, hashed)


# ---------------------------------------------------------------------------
# API-key keyed hashing (A5, concurrency-scaling-plan-2026-08-22.md §3.3)
# ---------------------------------------------------------------------------
#
# API-key secrets (services.api_key_service) are 192-bit random values, not
# human-chosen passwords, so bcrypt's deliberate slowness defends nothing;
# see core.config.api_key_hmac_secret's docstring for the full argument.
# HMAC-SHA256 keyed with a server-side secret is the replacement: forging a
# valid hash for a chosen plaintext requires knowing that key, and the
# comparison is constant-time via hmac.compare_digest.
#
# Stored format: ``"hmac-sha256$" + hex(HMAC-SHA256(api_key_hmac_secret(),
# plaintext))``. The literal prefix acts as a version marker so a mixed
# database (this key's rollout is expand-then-contract: new issuances write
# this format, but keys issued before this landed keep their bcrypt hash
# until they are next rotated) can tell which verifier a given row wants
# without a separate schema column. bcrypt hashes never collide with this
# prefix: passlib's bcrypt output always starts with ``$2a$``/``$2b$``/
# ``$2y$``, so the check is unambiguous.

API_KEY_HASH_SCHEME = "hmac-sha256"
_API_KEY_HASH_PREFIX = f"{API_KEY_HASH_SCHEME}$"


def hash_api_key_secret(plaintext: str) -> str:
    """Return the keyed HMAC-SHA256 hash stored for a NEW API-key secret.

    Used by :func:`services.api_key_service.issue_api_key` for every key
    minted after A5 landed. Existing rows keep whatever bcrypt hash
    :func:`hash_password` produced at issuance time; this function is not
    retroactive.
    """
    digest = hmac.new(
        api_key_hmac_secret().encode("utf-8"),
        plaintext.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{_API_KEY_HASH_PREFIX}{digest}"


def is_api_key_hmac_hash(hashed: str) -> bool:
    """True iff ``hashed`` is the new (A5) HMAC-SHA256 format.

    False for the legacy bcrypt format (and for any other unrecognized
    value); callers use this to route to the right verifier, and the
    legacy/unrecognized branch is the SAFE default: it falls through to
    bcrypt verification, which returns False rather than raising for a
    malformed hash (see :func:`verify_password`).
    """
    return hashed.startswith(_API_KEY_HASH_PREFIX)


def verify_api_key_hmac(plaintext: str, hashed: str) -> bool:
    """Constant-time verification against a :func:`hash_api_key_secret` hash.

    Returns False (never raises) if ``hashed`` is not in the expected
    ``hmac-sha256$<hex>`` shape, mirroring :func:`verify_password`'s
    fail-closed contract for a malformed stored value.
    """
    if not is_api_key_hmac_hash(hashed):
        return False
    expected_hex = hashed[len(_API_KEY_HASH_PREFIX) :]
    candidate = hmac.new(
        api_key_hmac_secret().encode("utf-8"),
        plaintext.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(candidate, expected_hex)


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
    # `require_iat`: every token this service mints carries `iat`, and the
    # password-change check reads it. Without this a token with the claim
    # stripped decodes cleanly and that check silently has no opinion, which
    # is the fail-open direction.
    claims: dict[str, Any] = jwt.decode(
        token,
        secret_key(),
        algorithms=[JWT_ALGORITHM],
        options={"require_iat": True},
    )
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
    # When this user's password last changed, carried so the permission cache
    # can be judged without re-reading the row. The cache is keyed by user id,
    # not by token, so a cached principal warmed by ANY token would otherwise
    # be handed to a token minted before the change: the entry is refilled by
    # the victim's own new session, so the stale token rides it for its whole
    # life rather than for the cache TTL. ``None`` for API-key principals,
    # which are a separate credential with their own revocation.
    password_changed_at: datetime | None = None


def highest_role(roles: list[str], *, is_superuser: bool) -> str:
    """The most privileged grade among a user's memberships.

    Public because the WebSocket path needs it too. It cannot use
    ``_load_current_user`` (a WebSocket scope carries no Request for the
    dependency to take) and so hand-copied this, priority map and all, with a
    comment saying the copy existed to avoid importing a private name. The copy
    then drifted: it was missing ``viewer``, and it did not inherit the
    password-change check added to the original. Exporting the function is the
    cheaper answer than keeping two of it.
    """
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
# "who is this and what may they do", which one statement (A3,
# concurrency-scaling-plan-2026-08-22.md §3.3) rebuilds on every
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
        # Judged against the value the cached principal carries. That value is
        # only trustworthy for entries built at or after the change, so
        # `set_password` drops the entry as it stamps the row: an entry warmed
        # BEFORE a change holds the old value (usually NULL) and would pass a
        # stolen token, because the cached copy does not know the password
        # moved.
        #
        # The drop is per-process and the deployment runs several workers, so
        # it is not the whole guarantee on its own. What closes the gap is that
        # every worker's entry expires within PERMISSION_CACHE_TTL_SECONDS
        # (capped at 300) and is then rebuilt from the row, and that the
        # rebuilt entry carries the new timestamp, so a refill cannot hand the
        # stolen token back its access the way it would if the check lived
        # only on the miss path.
        if password_change_invalidates(claims.get("iat"), cached.password_changed_at):
            return None
        _bind_audit_actor(cached)
        return cached

    # Local import — avoids a circular import at module load (models -> Base
    # -> auth which references nothing from us, but keeping the import lazy
    # makes the security module safe to import from anywhere).
    from models import Membership, User

    # A3 (concurrency-scaling-plan-2026-08-22.md §3.3): one statement instead
    # of two. ``joinedload`` pulls memberships through a LEFT OUTER JOIN
    # rather than a second SELECT, which is safe here because there is only
    # one collection being joined (no cross-product with a second collection)
    # and a user's membership count is bounded by the number of teams in the
    # organization: small, not the kind of fan-out joinedload warns against.
    # A joined collection load requires deduplicating the parent rows before
    # reading them off the result (SQLAlchemy 2.0 raises otherwise), hence
    # ``.unique()``.
    stmt = select(User).where(User.id == user_id).options(joinedload(User.memberships))
    result = (await session.execute(stmt)).unique()
    user = result.scalar_one_or_none()
    if user is None:
        return None

    # A token minted before the password changed is refused. The cached branch
    # above applies the same predicate against the value carried on the
    # principal: "populated only after this check" is not enough on its own,
    # because the entry is keyed by user and serves whichever token comes
    # next.
    if password_change_invalidates(claims.get("iat"), user.password_changed_at):
        return None

    memberships: list[Membership] = list(user.memberships)
    team_ids = [m.team_id for m in memberships]
    team_roles = {m.team_id: m.role for m in memberships}
    role = highest_role(
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
        # Carried so the cached branch above can judge a later token without
        # re-reading the row.
        password_changed_at=user.password_changed_at,
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
    "verify_password_async",
]
