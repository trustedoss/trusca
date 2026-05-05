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
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
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

# Role priority — higher value means more privileged.
_ROLE_PRIORITY: dict[str, int] = {
    "developer": 1,
    "team_admin": 2,
    "super_admin": 3,
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

    # Bind the user into the audit context so any flush triggered later in
    # this request gets actor_user_id populated automatically.
    ctx = dict(audit_context.get() or {})
    ctx["user_id"] = str(cu.id)
    audit_context.set(ctx)

    return cu


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
    "require_team_member",
    "verify_password",
]
