# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Auth request/response schemas — Phase 1 PR #5.

Pydantic v2. We deliberately split RegisterRequest from the ORM model so
incoming JSON cannot smuggle is_superuser/is_active flags. UserPublic is the
only shape ever returned to the wire — it never carries hashed_password.

Quality standard §3 (CLAUDE.md): the password field rejects values shorter
than 8 characters at the schema layer (NIST 800-63B minimum for user-chosen
secrets). The 422 response is automatically RFC 7807 because of the
validation handler installed in core.errors.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from core.password_policy import is_weak_password


def _reject_weak_password(value: str) -> str:
    """Shared validator: reject common/blocklisted passwords (H-8).

    Length is enforced by ``min_length`` (8, NIST 800-63B floor); this adds the
    other half of §5.1.1.2 — rejecting commonly-used / predictable values. The
    ``ValueError`` surfaces as an RFC 7807 422 via core.errors.
    """
    reason = is_weak_password(value)
    if reason:
        raise ValueError(reason)
    return value


class RegisterRequest(BaseModel):
    """Inbound payload for POST /auth/register."""

    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=256,
        description="At least 8 characters (NIST 800-63B minimum), not a common password.",
    )
    full_name: str | None = Field(default=None, max_length=255)

    _check_password_strength = field_validator("password")(_reject_weak_password)


class LoginRequest(BaseModel):
    """Inbound payload for POST /auth/login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MfaVerifyRequest(BaseModel):
    """Inbound payload for POST /auth/mfa/verify."""

    mfa_token: str = Field(min_length=1, max_length=4096)
    # Wide enough for a six-digit TOTP code or a recovery code with its
    # separator, and bounded so a caller cannot make the server hash something
    # enormous ten times over.
    code: str = Field(min_length=1, max_length=64)


class MfaEnrolStartResponse(BaseModel):
    """What the setup screen needs to draw a QR code.

    The secret is returned alongside the URI because somebody whose camera or
    scanner will not cooperate has to be able to type it in, and this is the
    only moment it can be shown: it is encrypted at rest and never read back
    out to a client again.
    """

    secret: str
    provisioning_uri: str
    mfa_token: str


class MfaStepUpRequest(BaseModel):
    """Proof that the person is present, not just that a session is.

    Either field. A code where the account already has a factor, the password
    otherwise or when the authenticator is not to hand. Both empty is refused
    rather than treated as the session being enough, because the session is
    exactly what a stolen token supplies.
    """

    password: str | None = Field(default=None, max_length=1024)
    code: str | None = Field(default=None, max_length=64)


class MfaEnrolCompleteRequest(BaseModel):
    """A code proving the authenticator app is working."""

    mfa_token: str = Field(min_length=1, max_length=4096)
    code: str = Field(min_length=1, max_length=64)


class RecoveryCodesResponse(BaseModel):
    """Shown once. They are stored as hashes, so this is the only readable form."""

    codes: list[str]


class UserPublic(BaseModel):
    """Shape returned for every user-bearing response. Never includes secrets."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime


class MembershipPublic(BaseModel):
    """One of the authenticated user's team memberships (for /auth/me)."""

    model_config = ConfigDict(from_attributes=True)

    team_id: uuid.UUID
    team_name: str
    role: str


class UserMeResponse(UserPublic):
    """``/auth/me`` — UserPublic plus the caller's team memberships.

    The frontend needs a ``team_id`` to create projects and scope writes.
    The base UserPublic (also returned by /register) stays minimal; the
    membership list lives only on the authenticated /me shape. ``memberships``
    is ordered oldest-first so ``memberships[0]`` is a stable default team
    (a self-registered user's auto-created team).
    """

    memberships: list[MembershipPublic] = Field(default_factory=list)
    mfa_enabled: bool = Field(
        default=False,
        description=(
            "Whether this account requires a second factor at sign-in. On the "
            "authenticated shape only: it tells the profile page which of the "
            "enrol and reissue actions to offer, and it is the account's own "
            "state rather than anything about another user."
        ),
    )


class TokenResponse(BaseModel):
    """Response body for /auth/login and /auth/refresh."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ---------------------------------------------------------------------------
# Phase 6 PR #18 — public password-reset flow.
#
# These complement (do NOT replace) the admin-initiated reset endpoint at
# ``POST /v1/admin/users/{id}/password-reset`` that landed in Phase 4 PR
# #13. The public flow is unauthenticated and MUST return uniform 204
# regardless of whether the email exists (CWE-204) — the schema layer only
# validates the inbound shape; the service layer is where the timing /
# enumeration defences live.
# ---------------------------------------------------------------------------


class ForgotPasswordRequest(BaseModel):
    """Inbound payload for POST /auth/forgot-password."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Inbound payload for POST /auth/reset-password.

    The new password reuses the registration policy (≥ 8 chars / NIST
    800-63B minimum). The token is a URL-safe string up to ~64 chars
    (``secrets.token_urlsafe(32)`` produces ~43 chars; we cap at 256 for
    defence in depth against pathological inputs).
    """

    token: str = Field(min_length=8, max_length=256)
    new_password: str = Field(
        min_length=8,
        max_length=256,
        description="At least 8 characters (NIST 800-63B minimum), not a common password.",
    )

    _check_new_password_strength = field_validator("new_password")(_reject_weak_password)
