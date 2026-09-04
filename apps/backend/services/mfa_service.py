# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""Enrolling in, verifying and clearing a second factor.

Every function here that changes something commits it. That is not a style
preference: two of these writes are the kind whose absence leaves the happy
path working. Recording the TOTP step is what prevents a replay, and if the
row never reaches the database the code still verifies, the sign-in still
succeeds, and the only way to notice is to present the same code twice and
watch it work both times. Spending a recovery code is the same shape.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core import totp
from core.crypto import decrypt_secret, encrypt_secret
from core.recovery_codes import generate_codes, normalise
from core.security import hash_password, verify_password_async
from models import User, UserRecoveryCode

log = structlog.get_logger("auth.mfa")

#: Its own key, not the one the forge credentials share. A stolen TOTP secret
#: generates a second factor for ever.
ENCRYPTION_PURPOSE = "totp"


class MfaError(Exception):
    """Base for the refusals this service makes."""


class MfaNotEnrolled(MfaError):
    """No secret stored, so there is nothing to verify against."""


class MfaAlreadyEnabled(MfaError):
    """Enrolling again would replace a working factor without proving anything."""


class InvalidMfaCode(MfaError):
    """Neither a valid TOTP code nor an unused recovery code."""


async def begin_enrolment(session: AsyncSession, *, user: User) -> tuple[str, str]:
    """Store a secret and return it with its provisioning URI.

    The secret is written now and ``mfa_enabled`` stays false. Enabling here
    would lock out anybody who closed the tab before their authenticator was
    working: the next sign-in would ask for a code they cannot produce, and the
    only way back would be an administrator.
    """
    if user.mfa_enabled:
        raise MfaAlreadyEnabled("second factor already enabled")

    secret = totp.generate_secret()
    user.mfa_secret_encrypted = encrypt_secret(secret, purpose=ENCRYPTION_PURPOSE)
    user.mfa_last_counter = None
    await session.commit()

    uri = totp.provisioning_uri(secret, account=user.email, issuer="TRUSCA")
    log.info("auth.mfa_enrolment_started", user_id=str(user.id))
    return secret, uri


async def complete_enrolment(
    session: AsyncSession, *, user: User, code: str
) -> list[str]:
    """Turn the factor on, once a code proves the app is working.

    Returns the recovery codes, which are shown once and never again: they are
    stored as hashes, so this return value is the only time they exist in a
    readable form.
    """
    if user.mfa_enabled:
        raise MfaAlreadyEnabled("second factor already enabled")
    if not user.mfa_secret_encrypted:
        raise MfaNotEnrolled("no enrolment in progress")

    secret = decrypt_secret(user.mfa_secret_encrypted, purpose=ENCRYPTION_PURPOSE)
    counter = totp.verify(secret, code)
    if counter is None:
        raise InvalidMfaCode("code did not match")

    user.mfa_enabled = True
    user.mfa_last_counter = counter
    user.mfa_changed_at = datetime.now(UTC)
    codes = await _issue_recovery_codes(session, user=user)
    await session.commit()

    log.info("auth.mfa_enabled", user_id=str(user.id))
    return codes


async def verify_second_factor(session: AsyncSession, *, user: User, code: str) -> None:
    """Accept a TOTP code or a recovery code, and spend it.

    Raises :class:`InvalidMfaCode` for anything else. Both branches write: the
    TOTP one records the step so the same code cannot be presented again, and
    the recovery one marks the row used. A caller that skipped the commit would
    see both succeed and neither take effect.
    """
    if not user.mfa_enabled or not user.mfa_secret_encrypted:
        raise MfaNotEnrolled("second factor is not enabled")

    secret = decrypt_secret(user.mfa_secret_encrypted, purpose=ENCRYPTION_PURPOSE)
    counter = totp.verify(secret, code)
    if counter is not None:
        # A code is valid for its whole thirty-second step, so accepting one
        # from a step already spent lets somebody who saw it use it again.
        if user.mfa_last_counter is not None and counter <= user.mfa_last_counter:
            log.warning("auth.mfa_replayed", user_id=str(user.id), counter=counter)
            raise InvalidMfaCode("code already used")
        user.mfa_last_counter = counter
        await session.commit()
        return

    if await _spend_recovery_code(session, user=user, candidate=code):
        return

    raise InvalidMfaCode("code did not match")


async def regenerate_recovery_codes(session: AsyncSession, *, user: User) -> list[str]:
    """Replace the unused codes with a fresh set.

    Only the unused ones. A spent code is already refused, and deleting it
    would erase what the account page shows; leaving an unused one alive would
    defeat the reason somebody regenerates, which is usually that they believe
    the old set leaked.
    """
    if not user.mfa_enabled:
        raise MfaNotEnrolled("second factor is not enabled")

    codes = await _issue_recovery_codes(session, user=user)
    await session.commit()
    log.info("auth.mfa_recovery_codes_regenerated", user_id=str(user.id))
    return codes


async def clear_for_user(session: AsyncSession, *, user: User) -> None:
    """Undo the enrolment completely, for an administrator unlocking somebody.

    All of it, not just the flag. Leaving the secret behind would let the
    account re-enable with the same one, and the reason somebody asks for this
    is usually that the device or the secret is gone.
    """
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_last_counter = None
    user.mfa_changed_at = datetime.now(UTC)

    for row in await _live_codes(session, user_id=user.id):
        await session.delete(row)

    await session.commit()
    log.info("auth.mfa_cleared", user_id=str(user.id))


async def _issue_recovery_codes(session: AsyncSession, *, user: User) -> list[str]:
    """Delete the unused rows and insert a fresh set. Does not commit."""
    for row in await _live_codes(session, user_id=user.id):
        await session.delete(row)

    codes = generate_codes()
    for code in codes:
        # bcrypt at cost 12, so issuing ten takes a couple of seconds. That is
        # paid once by the person enrolling and cannot be provoked by anyone
        # else; lowering the cost to make it quicker would weaken the codes.
        session.add(
            UserRecoveryCode(user_id=user.id, code_hash=hash_password(normalise(code)))
        )
    return codes


async def _live_codes(
    session: AsyncSession, *, user_id: uuid.UUID
) -> list[UserRecoveryCode]:
    result = await session.execute(
        select(UserRecoveryCode).where(
            UserRecoveryCode.user_id == user_id,
            UserRecoveryCode.used_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def _spend_recovery_code(
    session: AsyncSession, *, user: User, candidate: str
) -> bool:
    """Mark a matching unused code used. Commits, or returns False."""
    typed = normalise(candidate)
    if not typed:
        return False

    for row in await _live_codes(session, user_id=user.id):
        if await verify_password_async(typed, row.code_hash):
            row.used_at = datetime.now(UTC)
            await session.commit()
            log.info("auth.mfa_recovery_code_used", user_id=str(user.id))
            return True
    return False


__all__ = [
    "ENCRYPTION_PURPOSE",
    "InvalidMfaCode",
    "MfaAlreadyEnabled",
    "MfaError",
    "MfaNotEnrolled",
    "begin_enrolment",
    "clear_for_user",
    "complete_enrolment",
    "regenerate_recovery_codes",
    "verify_second_factor",
]
