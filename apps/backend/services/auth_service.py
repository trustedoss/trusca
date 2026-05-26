"""
Auth domain services — registration, login, refresh rotation, logout.

Phase 1 PR #5. The router layer (`api/v1/auth.py`) is intentionally thin: it
only translates HTTP shapes into service calls and turns domain exceptions
into RFC 7807 responses. All DB I/O lives here.

Refresh rotation contract (CLAUDE.md §3):
  - On each /auth/refresh hit we mark the presented token's row as
    revoked_reason='rotated' and insert a new row with parent_jti pointing
    back at the rotated row.
  - If a request presents a token whose row is already revoked (any reason),
    we treat it as reuse: revoke the entire ancestry chain
    (revoked_reason='reuse_detected') and return 401. This is the standard
    "refresh-token reuse detection" pattern from RFC 6819 §5.2.2.3.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import structlog
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from models import Membership, Organization, RefreshToken, Team, User

log = structlog.get_logger("auth.service")


# H-2 (security-reviewer blocker): timing-oracle fix for /auth/login.
#
# Without this, an attacker can probe whether an email exists by measuring the
# response time: the "user not found" branch skipped bcrypt entirely (~0 ms)
# while the "user found, wrong password" branch paid bcrypt cost 12 (~50 ms+).
# We pre-compute a dummy hash at module load and verify against it whenever no
# real user is found, so every branch pays the same bcrypt cost.
#
# Note re: CLAUDE.md rule #11 (no env vars at import time): this is a static
# computation derived from a literal string, not env-derived configuration.
# Allowed.
_DUMMY_BCRYPT_HASH: str = hash_password("dummy-anti-enum")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class AuthError(Exception):
    """Base class for auth-domain errors. Each carries an HTTP status."""

    status_code: int = 400
    title: str = "Auth Error"


class EmailAlreadyExists(AuthError):
    status_code = 409
    title = "Email Already Registered"


class InvalidCredentials(AuthError):
    status_code = 401
    title = "Invalid Credentials"


class InvalidRefreshToken(AuthError):
    status_code = 401
    title = "Invalid Refresh Token"


class RefreshReuseDetected(AuthError):
    status_code = 401
    title = "Refresh Token Reuse Detected"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def _register_creates_team() -> bool:
    """Whether self-registration provisions a personal org + team.

    Without a membership the new user has no ``team_id`` (``/auth/me`` returns
    an empty list) and cannot create projects — the form blocks with a "no
    team" notice. The demo / open-registration model (CLAUDE.md "데모 SaaS:
    가입 시 개인 Team 자동 생성") gives every signup their own space. On-prem
    org deployments that assign teams via an admin can disable this with
    ``AUTH_REGISTER_CREATES_TEAM=false``.
    """
    return os.getenv("AUTH_REGISTER_CREATES_TEAM", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


async def register_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str | None,
) -> User:
    """Create a new user. Raises EmailAlreadyExists on duplicate email.

    When ``AUTH_REGISTER_CREATES_TEAM`` is enabled (default) the user also gets
    a personal Organization + Team and a ``team_admin`` Membership so they can
    create projects immediately after signing up.
    """
    normalized_email = email.strip().lower()

    user = User(
        email=normalized_email,
        hashed_password=hash_password(password),
        full_name=full_name,
        is_active=True,
        is_superuser=False,
        is_verified=False,
    )
    session.add(user)
    try:
        # Flush (not commit) so we hold user.id for the personal team while the
        # whole signup stays in one transaction — a failed team provision must
        # roll the user back too.
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailAlreadyExists(f"email already registered: {normalized_email}") from exc

    if _register_creates_team():
        suffix = user.id.hex[:12]
        org = Organization(name=f"Personal org ({suffix})", slug=f"org-{suffix}")
        session.add(org)
        await session.flush()
        team = Team(
            organization_id=org.id,
            name=f"Personal team ({suffix})",
            slug=f"team-{suffix}",
        )
        session.add(team)
        await session.flush()
        session.add(Membership(user_id=user.id, team_id=team.id, role="team_admin"))

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailAlreadyExists(f"email already registered: {normalized_email}") from exc

    await session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
) -> User | None:
    """
    Return the User on success, None for any auth failure (no info leak).

    H-2: every branch verifies bcrypt against either the real hash or a
    pre-computed dummy hash so the response time does not reveal whether the
    email exists.
    """
    normalized_email = email.strip().lower()
    result = await session.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()

    hashed = user.hashed_password if user is not None else _DUMMY_BCRYPT_HASH
    password_ok = verify_password(password, hashed)

    if user is None or not user.is_active or not password_ok:
        return None
    return user


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


async def issue_token_pair(
    session: AsyncSession,
    *,
    user: User,
) -> tuple[str, str, datetime]:
    """
    Mint an access + refresh pair, persist the refresh row, and return
    (access_token, refresh_token, refresh_expires_at).
    """
    access_token = create_access_token(
        subject=str(user.id),
        role="super_admin" if user.is_superuser else None,
    )
    refresh_token, jti, expires_at = create_refresh_token(subject=str(user.id))

    session.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            token_hash=hash_refresh_token(refresh_token),
            parent_jti=None,
            expires_at=expires_at,
        )
    )

    # Update last_login_at — also gives the audit listener something to record.
    user.last_login_at = datetime.now(tz=UTC)

    await session.commit()
    return access_token, refresh_token, expires_at


# ---------------------------------------------------------------------------
# Refresh rotation + reuse detection
# ---------------------------------------------------------------------------


async def _revoke_chain(
    session: AsyncSession,
    *,
    leaf_jti: str,
    reason: str,
    now: datetime,
) -> None:
    """
    Walk the parent_jti pointers from a leaf upward and mark every link
    revoked with `reason`. Idempotent — already-revoked rows are skipped.
    """
    seen: set[str] = set()
    current: str | None = leaf_jti
    while current and current not in seen:
        seen.add(current)
        result = await session.execute(select(RefreshToken).where(RefreshToken.jti == current))
        row = result.scalar_one_or_none()
        if row is None:
            break
        if row.revoked_at is None:
            row.revoked_at = now
            row.revoked_reason = reason
        current = row.parent_jti


async def rotate_refresh(
    session: AsyncSession,
    *,
    raw_refresh: str,
) -> tuple[str, str, datetime, User]:
    """
    Validate the presented refresh, rotate it, and return a new pair.

    Returns (access_token, new_refresh_token, expires_at, user).

    - Raises InvalidRefreshToken if the JWT is malformed/expired/wrong type.
    - Raises RefreshReuseDetected if the row is already revoked. The whole
      chain (parents + this jti) is marked revoked_reason='reuse_detected'
      and committed before we raise.
    """
    if not raw_refresh:
        raise InvalidRefreshToken("missing refresh token")

    try:
        claims = decode_token(raw_refresh, expected_type=TOKEN_TYPE_REFRESH)
    except (JWTError, ValueError) as exc:
        raise InvalidRefreshToken("invalid refresh token") from exc

    jti = claims.get("jti")
    sub = claims.get("sub")
    if not jti or not sub:
        raise InvalidRefreshToken("malformed refresh token")

    result = await session.execute(select(RefreshToken).where(RefreshToken.jti == jti))
    row = result.scalar_one_or_none()
    if row is None:
        raise InvalidRefreshToken("unknown refresh token")

    now = datetime.now(tz=UTC)

    # Reuse detection — already revoked
    if row.revoked_at is not None:
        await _revoke_chain(session, leaf_jti=jti, reason="reuse_detected", now=now)
        await session.commit()
        log.warning("refresh_reuse_detected", jti=jti, user_id=str(row.user_id))
        raise RefreshReuseDetected("refresh token already used")

    # Expiration
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now:
        row.revoked_at = now
        row.revoked_reason = "expired"
        await session.commit()
        raise InvalidRefreshToken("refresh token expired")

    # Load the user
    user = (await session.execute(select(User).where(User.id == row.user_id))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise InvalidRefreshToken("user inactive")

    # Mark current row revoked, mint new pair pointing back via parent_jti.
    row.revoked_at = now
    row.revoked_reason = "rotated"

    new_access = create_access_token(
        subject=str(user.id),
        role="super_admin" if user.is_superuser else None,
    )
    new_refresh, new_jti, new_expires = create_refresh_token(subject=str(user.id), parent_jti=jti)
    session.add(
        RefreshToken(
            user_id=user.id,
            jti=new_jti,
            token_hash=hash_refresh_token(new_refresh),
            parent_jti=jti,
            expires_at=new_expires,
        )
    )

    await session.commit()
    return new_access, new_refresh, new_expires, user


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


async def revoke_refresh(
    session: AsyncSession,
    *,
    raw_refresh: str | None,
) -> None:
    """
    Revoke the presented refresh (logout).

    Idempotent — silently no-ops if the cookie is missing or unknown so
    /auth/logout always returns 204 even if called twice.
    """
    if not raw_refresh:
        return
    try:
        claims = decode_token(raw_refresh, expected_type=TOKEN_TYPE_REFRESH)
    except (JWTError, ValueError):
        return

    jti = claims.get("jti")
    if not jti:
        return
    result = await session.execute(select(RefreshToken).where(RefreshToken.jti == jti))
    row = result.scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        await session.commit()
        return
    row.revoked_at = datetime.now(tz=UTC)
    row.revoked_reason = "logout"
    await session.commit()
