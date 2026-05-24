"""
Pydantic schemas for GitHub App credential management — v2.2-b1.

Public shapes:
  - GitHubAppCredentialCreateIn   — request body for POST (the ONLY place the
                                    plaintext PEM private key is accepted).
  - GitHubAppCredentialOut        — response: metadata + ``has_private_key`` bool.
                                    NEVER includes the private key or any ciphertext.
  - GitHubAppCredentialListPage   — paginated list wrapper.
  - GitHubAppInstallationLinkIn   — request body for linking / opting-in an
                                    installation to a project.
  - GitHubAppInstallationOut      — installation row (no secrets at all).
  - GitHubAppInstallationListPage — paginated list wrapper.

Design notes:
  - The private key plaintext appears ONLY on the inbound ``GitHubAppCredentialCreateIn``;
    it is encrypted in the service before persist and never echoed back. Every
    response shape omits it by construction (a leaky serializer that round-trips
    an ORM row would still not surface plaintext — only the ciphertext column
    exists on the model, and these schemas don't map it).
  - PEM / app_id / slug / repo / account validators reject adversarial input
    (control chars, NUL, CRLF, oversized, junk schemes) so a malformed
    registration fails fast with a 422 RFC 7807 envelope rather than a 500 or a
    poisoned stored value (per memory feedback_adversarial_input_parametrize).
"""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Validation bounds / patterns
# ---------------------------------------------------------------------------

# GitHub App private keys are PKCS#1/#8 PEM; a 2048-bit key is ~1.7KB and a
# 4096-bit key ~3.2KB. 16KB is generous headroom while bounding a hostile body.
_MAX_PEM_BYTES = 16 * 1024
_PEM_BEGIN = "-----BEGIN"

# app_id is GitHub's numeric App id. We accept only digits (1..20 chars covers
# any plausible value) — a non-numeric app_id is a client error, not data.
_APP_ID_RE = re.compile(r"^[0-9]{1,20}$")

# Control chars (incl. NUL, CR, LF, tab) are never legitimate in these metadata
# fields. Rejecting them blocks log-injection / header-smuggling via stored values.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# "owner/name" — GitHub repo full name. Conservative charset; one slash.
_REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")

# Account login — GitHub usernames/orgs are alnum + single hyphens. We are a bit
# more permissive (allow ._-) but still reject schemes / paths / control chars.
_ACCOUNT_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")

# installation_id — GitHub returns a (large) integer. Store/accept digits only.
_INSTALLATION_ID_RE = re.compile(r"^[0-9]{1,32}$")


def _reject_control_chars(value: str, *, field: str) -> None:
    if _CONTROL_CHARS_RE.search(value):
        raise ValueError(f"{field} must not contain control characters")


# ---------------------------------------------------------------------------
# Credential create / read
# ---------------------------------------------------------------------------


class GitHubAppCredentialCreateIn(BaseModel):
    """Request body for registering a GitHub App credential.

    ``private_key`` is the plaintext PEM — the ONLY place it is accepted. It is
    encrypted before persist and never returned.
    """

    app_id: str = Field(..., min_length=1, max_length=64)
    app_slug: str | None = Field(default=None, max_length=255)
    private_key: str = Field(
        ...,
        min_length=1,
        description=(
            "The GitHub App PEM private key (plaintext). Accepted ONCE at "
            "registration, encrypted at rest, and never returned."
        ),
    )
    webhook_secret: str | None = Field(default=None, max_length=1024)

    @field_validator("app_id")
    @classmethod
    def _validate_app_id(cls, v: str) -> str:
        v = v.strip()
        if not _APP_ID_RE.match(v):
            raise ValueError("app_id must be a numeric GitHub App id")
        return v

    @field_validator("app_slug")
    @classmethod
    def _validate_app_slug(cls, v: str | None) -> str | None:
        if v is None:
            return None
        _reject_control_chars(v, field="app_slug")
        v = v.strip()
        return v or None

    @field_validator("private_key")
    @classmethod
    def _validate_private_key(cls, v: str) -> str:
        # Bound the size BEFORE any further inspection — a hostile multi-MB body
        # must not be normalised / scanned.
        if len(v.encode("utf-8")) > _MAX_PEM_BYTES:
            raise ValueError(f"private_key exceeds the maximum size of {_MAX_PEM_BYTES} bytes")
        stripped = v.strip()
        if not stripped.startswith(_PEM_BEGIN):
            raise ValueError("private_key must be a PEM block beginning with '-----BEGIN'")
        # PEM is base64 + the BEGIN/END armor; embedded NUL bytes are never valid
        # and signal a binary / smuggled payload.
        if "\x00" in stripped:
            raise ValueError("private_key must not contain NUL bytes")
        return stripped

    @field_validator("webhook_secret")
    @classmethod
    def _validate_webhook_secret(cls, v: str | None) -> str | None:
        if v is None:
            return None
        _reject_control_chars(v, field="webhook_secret")
        return v or None


class GitHubAppCredentialOut(BaseModel):
    """Response shape for a credential — metadata only, NEVER any key material.

    ``has_private_key`` is always True for a persisted credential (the column is
    NOT NULL); it is surfaced explicitly so the UI can render a "configured"
    state without the schema ever carrying the key or its ciphertext.
    """

    id: UUID
    team_id: UUID
    app_id: str
    app_slug: str | None
    has_private_key: bool
    has_webhook_secret: bool
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None


class GitHubAppCredentialListPage(BaseModel):
    """Paginated list of GitHub App credentials."""

    items: list[GitHubAppCredentialOut]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# Installation link / read
# ---------------------------------------------------------------------------


class GitHubAppInstallationLinkIn(BaseModel):
    """Request body for linking (opting-in) an installation to a project."""

    installation_id: str = Field(..., min_length=1, max_length=64)
    account_login: str | None = Field(default=None, max_length=255)
    repository_full_name: str | None = Field(default=None, max_length=512)
    project_id: UUID | None = Field(
        default=None,
        description="The TrustedOSS project this installation is opted-in to.",
    )

    @field_validator("installation_id")
    @classmethod
    def _validate_installation_id(cls, v: str) -> str:
        v = v.strip()
        if not _INSTALLATION_ID_RE.match(v):
            raise ValueError("installation_id must be a numeric GitHub installation id")
        return v

    @field_validator("account_login")
    @classmethod
    def _validate_account_login(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if not _ACCOUNT_LOGIN_RE.match(v):
            raise ValueError("account_login is not a valid GitHub account login")
        return v

    @field_validator("repository_full_name")
    @classmethod
    def _validate_repository_full_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if not _REPO_FULL_NAME_RE.match(v):
            raise ValueError("repository_full_name must be of the form 'owner/name'")
        return v


class GitHubAppInstallationOut(BaseModel):
    """Installation row — no secrets at all."""

    id: UUID
    credential_id: UUID
    installation_id: str
    account_login: str | None
    repository_full_name: str | None
    project_id: UUID | None
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GitHubAppInstallationListPage(BaseModel):
    """Paginated list of installations under a credential."""

    items: list[GitHubAppInstallationOut]
    total: int
    page: int
    page_size: int


__all__ = [
    "GitHubAppCredentialCreateIn",
    "GitHubAppCredentialListPage",
    "GitHubAppCredentialOut",
    "GitHubAppInstallationLinkIn",
    "GitHubAppInstallationListPage",
    "GitHubAppInstallationOut",
]
