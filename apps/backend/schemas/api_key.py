# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Pydantic schemas for API Key management — Phase 5 PR #16.

Public shapes:
  - APIKeyCreateIn        — request body for POST /v1/api-keys
  - APIKeyCreateOut       — response with the plaintext (returned ONCE)
  - APIKeyListItem        — list-row shape (no plaintext, no hash)
  - APIKeyListPage        — paginated list wrapper
  - APIKeyScope           — Literal type alias for the closed scope set

Design notes:
  - APIKeyCreateOut.raw_key is the plaintext bearer string
    (``tos_<prefix>_<secret>``). It is returned exactly once at issuance and
    intentionally NOT stored on the server side. The list endpoint NEVER echoes
    the plaintext — clients must capture it from the create response or rotate
    the key.
  - APIKeyListItem omits ``key_hash`` so a leaky serializer (e.g. a future bug
    that round-trips ORM rows directly) cannot accidentally surface the hash.
  - Literal types on ``scope`` give us crisp OpenAPI + Pydantic v2 validation;
    a bogus value fails fast with a 422 RFC 7807 envelope.
  - APIKeyListItem.last_used_at has interval resolution, not per-request
    resolution (concurrency-scaling-plan-2026-08-22.md A2). See its field
    description and ``core.config.api_key_last_used_at_update_interval_seconds``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Closed scope set. ``Literal`` is friendlier to OpenAPI than a Postgres ENUM
# round-trip — the API accepts only these three values.
APIKeyScope = Literal["org", "team", "project"]

# What a key may DO, as distinct from the scope above, which says what it may
# reach. Read-only keys are refused every unsafe HTTP method at the auth path.
APIKeyPermissionBreadth = Literal["read_write", "read_only"]
API_KEY_PERMISSION_BREADTHS: tuple[str, ...] = ("read_write", "read_only")


class APIKeyCreateIn(BaseModel):
    """Request body for creating a new API key.

    The CHECK constraint on the DB enforces scope coherence; we mirror it in
    Pydantic for fast client-side feedback. The router pre-validates that the
    actor can actually issue at the requested scope (super_admin → org,
    team_admin → team, team member → project).
    """

    name: str = Field(..., min_length=1, max_length=100)
    scope: APIKeyScope
    permission_breadth: APIKeyPermissionBreadth = Field(
        default="read_only",
        description=(
            "What the key may do: 'read_only' (the default) or 'read_write'. "
            "A read-only key is refused every request that changes something, "
            "so a pipeline that just reads results cannot start a scan. Keys "
            "issued before this existed are read-write and stay that way."
        ),
    )
    team_id: UUID | None = None
    project_id: UUID | None = None
    service_account_id: UUID | None = Field(
        default=None,
        description=(
            "Issue the key to an automation identity instead of to yourself. "
            "The key then lives as long as that identity does, rather than "
            "stopping when you are deactivated. Omit for a personal key, "
            "which keeps today's behaviour exactly."
        ),
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=1825,  # 5 years
        description=(
            "Optional TTL in days. The key stops authenticating after this many "
            "days. Omit for a non-expiring key (CI keys should set one and "
            "rotate). Max 1825 (5 years)."
        ),
    )


class APIKeyNarrowIn(BaseModel):
    """Request body for PATCH /v1/api-keys/{id}.

    Only one value is accepted. Widening is not a validation failure to be
    argued with but a different operation: issue a new key. Spelling it as a
    literal keeps that in the OpenAPI contract rather than in a comment.
    """

    model_config = ConfigDict(extra="forbid")

    permission_breadth: Literal["read_only"] = Field(
        description=(
            "The only accepted value. Breadth narrows and never widens: a key "
            "that has been sitting in a CI log should not be handed more "
            "privilege than it was issued with."
        )
    )


class APIKeyCreateOut(BaseModel):
    """Response from POST /v1/api-keys.

    ``raw_key`` is the only place the plaintext is ever surfaced. The client
    is responsible for storing it (e.g. as a CI secret); a subsequent GET on
    the key returns the metadata-only :class:`APIKeyListItem` shape.
    """

    id: UUID
    key_prefix: str
    name: str
    scope: APIKeyScope
    permission_breadth: APIKeyPermissionBreadth = Field(
        description="What the key that was just issued may do."
    )
    team_id: UUID | None
    project_id: UUID | None
    created_by_user_id: UUID | None
    created_at: datetime
    expires_at: datetime | None = None
    raw_key: str = Field(
        ...,
        description=(
            "The plaintext bearer key (format: tos_<prefix>_<secret>). "
            "Returned exactly once at issuance; capture it client-side. "
            "Subsequent reads only return metadata."
        ),
    )


class APIKeyListItem(BaseModel):
    """List-row shape — never includes the plaintext or the hash."""

    id: UUID
    key_prefix: str
    name: str
    scope: APIKeyScope
    permission_breadth: APIKeyPermissionBreadth = Field(
        default="read_write",
        description=(
            "What this key may do. Rows issued before this existed read as "
            "'read_write', which is the breadth they have always had."
        ),
    )
    team_id: UUID | None
    project_id: UUID | None
    created_by_user_id: UUID | None
    created_by_email: str | None = Field(
        default=None,
        description=(
            "Email of the issuing user, so the management UI can show a "
            "human-readable creator column. None when the issuer account was "
            "deleted (created_by_user_id was SET NULL) or the user row is "
            "otherwise gone. PII note: this list is only reachable through the "
            "key-governance visibility boundary (issuer / team members / "
            "super_admin), so the email is not exposed beyond actors who can "
            "already manage the key."
        ),
    )
    created_at: datetime
    last_used_at: datetime | None = Field(
        default=None,
        description=(
            "When this key was last used to authenticate, rounded down to "
            "the nearest update interval (15 minutes by default, "
            "API_KEY_LAST_USED_AT_UPDATE_INTERVAL_SECONDS). This means "
            "'used at some point within that interval', not the exact "
            "instant of the most recent request. A key used twice inside "
            "one interval keeps the first commit's value. None means the "
            "key has never authenticated a request."
        ),
    )
    revoked_at: datetime | None
    expires_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class APIKeyListPage(BaseModel):
    """Paginated list of API keys."""

    items: list[APIKeyListItem]
    total: int
    page: int
    page_size: int


class APIKeyHashMigrationOut(BaseModel):
    """Response for ``GET /v1/admin/api-keys/hash-migration`` (A5).

    concurrency-scaling-plan-2026-08-22.md §3.3 A5: API-key hashing moved
    from bcrypt to a fast keyed HMAC-SHA256, expand-first. A key issued
    before that change keeps its bcrypt hash until it is reissued (there is
    no bulk-migration job), so this endpoint gives an operator the count
    needed to confirm "every active key has moved" before the follow-up
    change that drops bcrypt-hash reads from the auth path.
    """

    legacy_bcrypt_count: int = Field(
        description=(
            "Active (not revoked, not expired) API keys still hashed with "
            "the legacy bcrypt format. Zero means it is safe to schedule "
            "the contraction step (dropping bcrypt reads from the "
            "authentication path)."
        )
    )
    hmac_sha256_count: int = Field(
        description="Active API keys already hashed with the new HMAC-SHA256 format."
    )
    active_total: int = Field(description="legacy_bcrypt_count + hmac_sha256_count.")


__all__ = [
    "APIKeyCreateIn",
    "APIKeyCreateOut",
    "APIKeyHashMigrationOut",
    "APIKeyListItem",
    "APIKeyListPage",
    "APIKeyScope",
]
