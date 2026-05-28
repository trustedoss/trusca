"""
OAuth identity model — Phase 8 PR #23.

Table:
  - ``oauth_identities`` — one row per (User, external OAuth account). A single
    User can hold multiple identities (e.g. GitHub + Google) so the demo SaaS
    can let users link additional providers after their initial sign-in.

Conventions (CLAUDE.md core rules + existing model files):
  - PostgreSQL only. UUID PKs default to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ for every timestamp; ``linked_at`` on every row.
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - Closed enum (``provider``) uses a native Postgres ENUM type
    (``oauth_provider``); the migration owns ``CREATE TYPE`` so the model binds
    with ``create_type=False``.
  - No environment access at import time (CLAUDE.md core rule #11).

Security contract:
  - The ``UNIQUE (provider, provider_user_id)`` constraint is the canonical
    account-takeover guard: a second User cannot link the same external
    account because the INSERT collides at the DB layer. The service treats
    the ``IntegrityError`` as the "external identity already linked" signal —
    NO SELECT-then-INSERT (TOCTOU race).
  - ``provider_user_id`` is the provider's stable identifier:
      * GitHub → numeric ``id`` from ``GET /user`` (stored as string for type
        homogeneity across providers).
      * Google → ``sub`` claim from the OIDC userinfo endpoint.
    Email is NOT the primary key for an OAuth identity because providers
    allow users to change their primary email — the stable id stays.
  - ``email`` is per-identity (not foreign-keyed to users.email) so that a
    user with linked GitHub no-reply email + Google personal email can keep
    both for audit / forensic correlation with provider audit logs.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")

# Closed provider set — encoded as a Postgres native ENUM. The migration
# (0010) owns ``CREATE TYPE``; here we bind with ``create_type=False`` so
# SQLAlchemy never emits its own.
OAUTH_PROVIDER_VALUES = ("github", "google")


def _oauth_provider_enum() -> PG_ENUM:
    return PG_ENUM(
        *OAUTH_PROVIDER_VALUES,
        name="oauth_provider",
        create_type=False,
    )


# ---------------------------------------------------------------------------
# OAuthIdentity
# ---------------------------------------------------------------------------


class OAuthIdentity(Base):
    """
    External OAuth account linked to one TrustedOSS User.

    Lifecycle:
      - INSERT on first sign-in via that provider (``services.oauth_service``
        creates the row inside the same transaction that creates the User
        for new accounts, or links to an existing User by primary email).
      - UPDATE on every subsequent sign-in: ``last_login_at = now()``
        (and ``email`` / ``avatar_url`` are refreshed if the provider's
        copy changed). The audit listener captures the diff.
      - DELETE only via ``ON DELETE CASCADE`` when the User is deleted —
        we deliberately do not expose an "unlink" admin endpoint in this
        PR; that is a Phase 8+ follow-up.
    """

    __tablename__ = "oauth_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ENUM('github', 'google'). The migration owns CREATE TYPE.
    provider: Mapped[str] = mapped_column(_oauth_provider_enum(), nullable=False)

    # Provider's stable identifier (GitHub: numeric ``id``; Google: OIDC
    # ``sub``). Stored as VARCHAR(128) for cross-provider type homogeneity
    # and ample headroom — Google ``sub`` is currently 21 digits, GitHub
    # ``id`` is a 64-bit integer (≤ 20 chars).
    provider_user_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # Per-identity email surfaced from the provider. May differ from
    # ``users.email`` (e.g. a GitHub no-reply address). Plain VARCHAR
    # because uniqueness lives on ``(provider, provider_user_id)``.
    email: Mapped[str] = mapped_column(String(255), nullable=False)

    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Account-takeover guard: a single external account can map to
        # exactly one TrustedOSS User. The service catches the
        # IntegrityError on INSERT and translates it to a 409 Problem
        # Details response.
        UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_oauth_identities_provider_pid",
        ),
        Index("ix_oauth_identities_user_id", "user_id"),
        Index("ix_oauth_identities_email", "email"),
    )


__all__ = [
    "OAUTH_PROVIDER_VALUES",
    "OAuthIdentity",
]
