"""
Pydantic schemas for self-service OAuth identity management — Chore G.

Public shapes (frozen contract — frontend depends on these byte-for-byte):

  - ``OAuthIdentityOut``           — single identity row in list responses.
  - ``OAuthIdentityListResponse``  — list wrapper (``items: [...]``).

Why a separate schema module from the OAuth flow?
  - The auth flow under ``/auth/oauth/{provider}/...`` is anonymous; its
    surface is 302 redirects, not JSON. Identity *management* is a
    different surface: caller-scoped self-service (``/v1/users/me/...``)
    that returns/mutates JSON. Keeping the schemas split keeps each
    concern's wire shape obvious.

Field mapping notes:
  - The ORM model column ``OAuthIdentity.email`` is surfaced on the wire
    as ``provider_email`` to make it unambiguous which email is the user
    account email vs. the per-identity email — they can legitimately
    differ (GitHub no-reply vs. Google personal). The Pydantic alias
    keeps the wire contract while ``from_attributes`` reads the ORM
    column name directly.
  - ``created_at`` mirrors the model's ``linked_at`` column (renamed for
    consistency with other "list of things the user owns" responses).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Closed provider set — mirrors ``models.oauth_identity.OAUTH_PROVIDER_VALUES``.
# Hard-coded as a Literal so the OpenAPI schema renders an enum without
# leaking the Postgres ENUM machinery.
OAuthProvider = Literal["github", "google"]


class OAuthIdentityOut(BaseModel):
    """One linked OAuth identity in the self-service list response.

    Frozen contract — every field name + shape is depended on by the SPA
    profile page. Add new fields as nullable; never rename.
    """

    id: UUID
    provider: OAuthProvider
    provider_user_id: str
    provider_email: str | None = Field(
        default=None,
        validation_alias="email",
        serialization_alias="provider_email",
    )
    created_at: datetime = Field(
        validation_alias="linked_at",
        serialization_alias="created_at",
    )

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class OAuthIdentityListResponse(BaseModel):
    """Response wrapper for ``GET /v1/users/me/oauth-identities``.

    Sorted oldest-first; the caller renders the list as "first connected
    on T". The response has no pagination — a single user is unlikely to
    accumulate enough identities to need it (GitHub + Google + maybe a
    future SSO IdP).
    """

    items: list[OAuthIdentityOut]


__all__ = [
    "OAuthIdentityListResponse",
    "OAuthIdentityOut",
    "OAuthProvider",
]
