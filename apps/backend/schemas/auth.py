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

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    """Inbound payload for POST /auth/register."""

    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=256,
        description="At least 8 characters (NIST 800-63B minimum).",
    )
    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    """Inbound payload for POST /auth/login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


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
        description="At least 8 characters (NIST 800-63B minimum).",
    )
