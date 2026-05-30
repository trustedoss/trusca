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
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Closed scope set. ``Literal`` is friendlier to OpenAPI than a Postgres ENUM
# round-trip — the API accepts only these three values.
APIKeyScope = Literal["org", "team", "project"]


class APIKeyCreateIn(BaseModel):
    """Request body for creating a new API key.

    The CHECK constraint on the DB enforces scope coherence; we mirror it in
    Pydantic for fast client-side feedback. The router pre-validates that the
    actor can actually issue at the requested scope (super_admin → org,
    team_admin → team, team member → project).
    """

    name: str = Field(..., min_length=1, max_length=100)
    scope: APIKeyScope
    team_id: UUID | None = None
    project_id: UUID | None = None
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
    team_id: UUID | None
    project_id: UUID | None
    created_by_user_id: UUID | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class APIKeyListPage(BaseModel):
    """Paginated list of API keys."""

    items: list[APIKeyListItem]
    total: int
    page: int
    page_size: int


__all__ = [
    "APIKeyCreateIn",
    "APIKeyCreateOut",
    "APIKeyListItem",
    "APIKeyListPage",
    "APIKeyScope",
]
