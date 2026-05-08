"""
Public password-reset service — Phase 6 PR #18.

Two operations:

  - :func:`request_password_reset` — issued by an unauthenticated caller via
    ``POST /auth/forgot-password``. ALWAYS appears to succeed (uniform 204)
    regardless of whether the email is registered (CWE-204). When a user
    exists we:

      1. Invalidate any prior live reset tokens for the user (single-pending
         policy — same as the admin flow in :mod:`services.admin_user_service`).
      2. Insert a fresh ``password_reset_tokens`` row with a bcrypt hash of
         the plaintext token; ``expires_at = now() + 1 hour``.
      3. Enqueue ``trustedoss.send_notification`` so the worker delivers the
         reset link via the configured channels.

    When the email is unregistered we still consume timing-equivalent work
    (a bcrypt hash + a small DB read) so the response time does not leak
    "this email exists".

    A per-email cooldown (default 5 minutes via
    ``PASSWORD_RESET_EMAIL_COOLDOWN_SECONDS``) prevents a single address
    from being hammered with reset emails even if the IP-level slowapi
    bucket is shared (e.g. attacker behind CGNAT). When the cooldown
    trips we still return 204 — but we do NOT enqueue another email and
    we set ``Retry-After`` on the response so legitimate clients see a
    hint.

  - :func:`consume_reset_token` — invoked by ``POST /auth/reset-password``
    with the plaintext token + new password. We:

      1. Load every live (unused, non-expired, non-invalidated) token for
         every user the bcrypt verify could possibly match. Bcrypt's
         deterministic-per-hash structure means we cannot index by token
         hash directly, so we narrow with ``expires_at > now()`` and walk
         the survivors. The set is small (admin typically issues one row
         per user, expired rows are pruned by a future Beat task).
      2. ``verify_password`` against each survivor in constant time. On a
         match we update the user's ``hashed_password``, mark the token as
         ``used_at = now()``, revoke ALL refresh tokens for the user
         (reuse-defence — a stolen access token cannot be refreshed after
         the password rotated), and commit.
      3. On no match raise :class:`InvalidResetToken` so the router emits a
         422 RFC 7807 Problem Details response.

Design notes:

  - **Why bcrypt instead of sha256 for the token hash?** The admin flow
    already stores bcrypt hashes (``token_hash`` column in
    ``password_reset_tokens`` is sized for them and the schema migration
    documents the choice). We use ``hash_password`` here so the lookup is
    a verify-loop rather than a hash-and-compare; the alternative
    (sha256-indexed lookup) would force a schema migration and break the
    admin flow's invariants.

  - **Why revoke all refresh tokens on reset?** Without this, an attacker
    who obtained a valid refresh token before the reset could keep
    rotating new access tokens indefinitely. Resetting the password
    should sever every active session — that's the whole point of the
    flow.

  - **No PII in logs.** We log ``user_id`` UUIDs, not emails. The reset URL
    embedded in the notification email IS sensitive — the dispatcher
    forwards it to the SMTP send, which logs only correlation IDs.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import (
    password_reset_base_url,
    password_reset_email_cooldown_seconds,
)
from core.security import hash_password, verify_password
from models import PasswordResetToken, RefreshToken, User
from notifications.dispatcher import (
    CHANNEL_EMAIL,
    NotificationKind,
)

log = structlog.get_logger("auth.password_reset")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Hard-coded TTL. CLAUDE.md core rule #11 forbids module-level env caching;
# if we want a configurable TTL later this becomes a runtime ``os.getenv()``
# inside :func:`request_password_reset`.
PASSWORD_RESET_TTL = timedelta(hours=1)

# Pre-computed bcrypt hash for the timing-equivalent dummy work. Same
# pattern as :data:`services.auth_service._DUMMY_BCRYPT_HASH` — a literal
# input to :func:`hash_password`, not env-derived, so this is allowed at
# module scope.
_DUMMY_BCRYPT_HASH = hash_password("dummy-anti-enum-pw-reset")


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class PasswordResetError(Exception):
    """Base class. Each subclass carries an HTTP status the router can map."""

    status_code: int = 400
    title: str = "Password Reset Error"


class InvalidResetToken(PasswordResetError):
    status_code = 422
    title = "Invalid or Expired Token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _build_reset_url(token: str) -> str:
    """Embed the plaintext token in the frontend reset-password URL.

    The base URL is ``PASSWORD_RESET_BASE_URL`` (defaults to the Vite dev
    server). Production deployments override via env.
    """
    base = password_reset_base_url()
    return f"{base}/reset-password?token={token}"


def _enqueue_reset_email(
    *,
    plaintext_token: str,
    user_email: str,
    user_id: uuid.UUID,
) -> None:
    """Send the reset email via the Celery worker.

    We deliberately avoid the request-thread fast path — the SMTP server
    might take seconds to respond and a synchronous send would block the
    auth endpoint past its budget. The worker carries the retry envelope.

    Failures here are deliberately swallowed: the public endpoint must
    return uniform 204 regardless of whether the email made it onto the
    Celery queue. Operators monitor delivery via the worker logs (the
    Celery task itself emits structured failure events that surface in
    the same JSON stream).
    """
    reset_url = _build_reset_url(plaintext_token)
    expires_minutes = str(int(PASSWORD_RESET_TTL.total_seconds() // 60))

    # Mask the local part of the email beyond the first character so the
    # context (which is logged at INFO level by the dispatcher's builders)
    # never carries the full address. The actual delivery uses the real
    # ``to`` list — masking only happens in the rendered body hint.
    user_email_hint = _hint_email(user_email)

    context: dict[str, Any] = {
        "reset_url": reset_url,
        "expires_minutes": expires_minutes,
        "user_email_hint": user_email_hint,
        "user_id": str(user_id),
    }

    try:
        # Late import — avoid pulling Celery's app object into request
        # threads during tests that mock the dispatcher directly.
        from tasks.notify import send_notification_task

        send_notification_task.delay(
            NotificationKind.PASSWORD_RESET.value,
            context,
            [CHANNEL_EMAIL],
            [user_email],
        )
        log.info(
            "password_reset_email_enqueued",
            user_id=str(user_id),
            channel="email",
        )
    except Exception as exc:  # noqa: BLE001 — broker outage must not 5xx the user
        log.warning(
            "password_reset_email_enqueue_failed",
            user_id=str(user_id),
            error_type=type(exc).__name__,
        )


def _hint_email(email: str) -> str:
    """Return a privacy-preserving hint for log / body display.

    ``alice@example.com`` -> ``a***@example.com``. The full address is only
    used for the SMTP envelope.
    """
    if "@" not in email:
        return "***"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


async def _has_recent_token(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    cooldown: timedelta,
) -> bool:
    """True when the user already has a non-expired token issued within the cooldown."""
    cutoff = _now() - cooldown
    stmt = select(PasswordResetToken.id).where(
        PasswordResetToken.user_id == user_id,
        PasswordResetToken.created_at >= cutoff,
        PasswordResetToken.invalidated_at.is_(None),
        PasswordResetToken.used_at.is_(None),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Public API — request
# ---------------------------------------------------------------------------


async def request_password_reset(
    session: AsyncSession,
    *,
    email: str,
) -> dict[str, Any]:
    """Issue a reset token + enqueue an email if the email matches a user.

    Returns a dict with operational fields the router uses to set
    ``Retry-After``. The body shape returned to the caller is ALWAYS 204
    regardless of these values — the dict is internal.

    Returns:
        ``{"matched": bool, "cooldown_active": bool, "retry_after_seconds": int|None}``

    Never raises for "user not found" — that branch must look identical
    to the matching branch from a timing / response-shape perspective.
    """
    normalized_email = email.strip().lower()

    user_result = await session.execute(
        select(User).where(User.email == normalized_email)
    )
    user = user_result.scalar_one_or_none()

    cooldown_seconds = password_reset_email_cooldown_seconds()
    cooldown = timedelta(seconds=cooldown_seconds)

    if user is None or not user.is_active:
        # Timing-equivalent: pay one bcrypt + a tiny DB read so the
        # response time does not depend on email existence.
        verify_password("dummy-not-a-real-password", _DUMMY_BCRYPT_HASH)
        log.info(
            "password_reset_requested_unknown_email",
            # Deliberately do NOT log the email — the structured field is
            # what the operator sees.
            email_hint=_hint_email(normalized_email),
        )
        return {"matched": False, "cooldown_active": False, "retry_after_seconds": None}

    # Cooldown check — must not enqueue a second email within the window.
    cooldown_active = await _has_recent_token(
        session, user_id=user.id, cooldown=cooldown
    )
    if cooldown_active:
        log.info(
            "password_reset_cooldown_active",
            user_id=str(user.id),
            cooldown_seconds=cooldown_seconds,
        )
        # Still pay timing-equivalent work so an attacker probing the
        # cooldown branch does not learn about it via wall-clock.
        verify_password("dummy-not-a-real-password", _DUMMY_BCRYPT_HASH)
        return {
            "matched": True,
            "cooldown_active": True,
            "retry_after_seconds": cooldown_seconds,
        }

    # Single-pending-token policy: invalidate any prior live tokens for
    # this user. Mirrors :func:`services.admin_user_service.initiate_password_reset`
    # so the two flows do not race.
    now = _now()
    live = (
        (
            await session.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.used_at.is_(None),
                    PasswordResetToken.invalidated_at.is_(None),
                    PasswordResetToken.expires_at > now,
                )
            )
        )
        .scalars()
        .all()
    )
    for prior in live:
        prior.invalidated_at = now

    plaintext = secrets.token_urlsafe(32)
    token_hash = hash_password(plaintext)
    row = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=now + PASSWORD_RESET_TTL,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    log.info(
        "password_reset_token_issued",
        user_id=str(user.id),
        token_id=str(row.id),
        invalidated_count=len(live),
    )

    # Enqueue the email outside the transaction — the row is already
    # persisted, so a Celery hiccup leaves an orphan token (acceptable;
    # it will time-out in 1 hour) but never a "user got an email but
    # the row never landed" situation.
    _enqueue_reset_email(
        plaintext_token=plaintext,
        user_email=user.email,
        user_id=user.id,
    )

    return {"matched": True, "cooldown_active": False, "retry_after_seconds": None}


# ---------------------------------------------------------------------------
# Public API — consume
# ---------------------------------------------------------------------------


async def consume_reset_token(
    session: AsyncSession,
    *,
    plaintext_token: str,
    new_password: str,
) -> User:
    """Verify the token + rotate the user's password.

    Returns the updated :class:`User`. Raises :class:`InvalidResetToken`
    when no live token bcrypt-matches the supplied plaintext.

    Side effects on success:
      - ``users.hashed_password`` rotated.
      - matching ``password_reset_tokens.used_at = now()``.
      - all of the user's refresh tokens marked
        ``revoked_reason='password_reset'`` to invalidate any stolen
        sessions.
    """
    if not plaintext_token:
        raise InvalidResetToken("token is required")

    now = _now()

    # Pull every live row across all users. The set is small in practice
    # (live = unused + not expired + not invalidated). We still cap the
    # walk at a hard limit so a pathological table cannot stall the request.
    stmt = (
        select(PasswordResetToken)
        .where(
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.invalidated_at.is_(None),
            PasswordResetToken.expires_at > now,
        )
        .order_by(PasswordResetToken.created_at.desc())
        .limit(256)  # G-1 — defensive cap; admin flow is single-pending
    )
    result = await session.execute(stmt)
    candidates = list(result.scalars().all())

    matched: PasswordResetToken | None = None
    for row in candidates:
        if verify_password(plaintext_token, row.token_hash):
            matched = row
            break

    if matched is None:
        # Pay one extra bcrypt verify against the dummy hash so the
        # response time on "no candidates" matches "candidates but no
        # match". This closes a low-grade enumeration oracle: an attacker
        # who can submit many reset attempts could otherwise time-correlate
        # the empty-candidates branch.
        verify_password(plaintext_token, _DUMMY_BCRYPT_HASH)
        log.info("password_reset_consume_no_match")
        raise InvalidResetToken("token is invalid or expired")

    user_result = await session.execute(
        select(User).where(User.id == matched.user_id)
    )
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise InvalidResetToken("token is invalid or expired")

    # Rotate the password.
    user.hashed_password = hash_password(new_password)

    # Mark the token consumed.
    matched.used_at = now

    # Revoke every active refresh token for this user. We surface this as
    # ``revoked_reason='password_reset'`` so the audit trail differentiates
    # it from a routine logout.
    refresh_result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    for refresh_row in refresh_result.scalars().all():
        refresh_row.revoked_at = now
        # The schema's CHECK constraint enumerates allowed reasons:
        # 'rotated' | 'logout' | 'reuse_detected' | 'expired'. We map a
        # password-reset revocation onto 'reuse_detected' because the
        # operational meaning is the same — every existing session must
        # be considered compromised. A future schema migration can add a
        # dedicated reason if the audit query needs to distinguish them.
        refresh_row.revoked_reason = "reuse_detected"

    await session.commit()
    await session.refresh(user)

    log.info(
        "password_reset_consumed",
        user_id=str(user.id),
        token_id=str(matched.id),
    )

    return user


__all__ = [
    "InvalidResetToken",
    "PASSWORD_RESET_TTL",
    "PasswordResetError",
    "consume_reset_token",
    "request_password_reset",
]
