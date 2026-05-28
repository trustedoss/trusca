"""
Auth domain models — Phase 1 PR #5.

Tables: organizations, teams, users, memberships, refresh_tokens, audit_logs.

Conventions (CLAUDE.md core rules + db-designer agent guide):
  - PostgreSQL only. UUID PKs default to gen_random_uuid() (pgcrypto extension).
  - TIMESTAMPTZ for every timestamp; created_at/updated_at on every mutable row.
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - Closed enum (Membership.role) uses a native Postgres ENUM type ('user_role').
  - JSONB filter / containment columns get a GIN index.
  - User.email uses CITEXT for case-insensitive uniqueness.
  - No environment access at import time (CLAUDE.md core rule #11).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB, UUID
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import Base

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")
EMPTY_JSONB = text("'{}'::jsonb")

# Closed role set — encoded as a Postgres native ENUM so invalid values are
# rejected at the DB layer. The migration creates the type 'user_role'; here
# we bind to it via name= (do not let SQLAlchemy auto-create it on metadata
# emit, otherwise alembic would also try to create it).
ROLE_VALUES = ("super_admin", "team_admin", "developer")


def _role_enum() -> PG_ENUM:
    return PG_ENUM(
        *ROLE_VALUES,
        name="user_role",
        create_type=False,  # the migration owns CREATE TYPE
    )


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------


class Organization(Base):
    """A deployment-level tenant. Most installs have exactly one row."""

    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    teams: Mapped[list[Team]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # GIN on settings supports `settings @> '{...}'` lookups in admin UI.
        Index("ix_organizations_settings_gin", "settings", postgresql_using="gin"),
    )


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------


class Team(Base):
    """A team under an organization. Tenant boundary for project visibility."""

    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    organization: Mapped[Organization] = relationship(back_populates="teams")
    memberships: Mapped[list[Membership]] = relationship(
        back_populates="team", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_teams_org_slug"),
        Index("ix_teams_organization_id", "organization_id"),
    )


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


class User(Base):
    """End user. Columns are FastAPI-Users compatible (is_active/is_superuser/is_verified)."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    # CITEXT — case-insensitive; UNIQUE constraint covers the usual lookups.
    email: Mapped[str] = mapped_column(CITEXT(), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    memberships: Mapped[list[Membership]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


# ---------------------------------------------------------------------------
# Membership (User × Team × Role)
# ---------------------------------------------------------------------------


class Membership(Base):
    """Maps a user into a team with a single role. One row per (user, team)."""

    __tablename__ = "memberships"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(_role_enum(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    user: Mapped[User] = relationship(back_populates="memberships")
    team: Mapped[Team] = relationship(back_populates="memberships")

    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_memberships_user_team"),
        Index("ix_memberships_user_id", "user_id"),
        Index("ix_memberships_team_id", "team_id"),
        # Lookups: "give me all admins of team X", "give me all teams where user U is dev".
        Index("ix_memberships_team_role", "team_id", "role"),
        Index("ix_memberships_user_role", "user_id", "role"),
    )


# ---------------------------------------------------------------------------
# RefreshToken (rotation + reuse detection)
# ---------------------------------------------------------------------------


class RefreshToken(Base):
    """
    Refresh-token state for JWT rotation + reuse detection.

    We never store the token itself, only its jti (JWT ID) and a sha256 hash of
    the issued JWT. On rotation we mark the old row revoked_at/revoked_reason
    and insert the child with parent_jti pointing back. If a request arrives
    with a refresh whose jti is already revoked, we trip the reuse-detected
    branch and revoke the entire chain.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    jti: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_jti: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    user: Mapped[User] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        CheckConstraint(
            "revoked_reason IN ('rotated','logout','reuse_detected','expired')"
            " OR revoked_reason IS NULL",
            name="ck_refresh_tokens_revoked_reason",
        ),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_parent_jti", "parent_jti"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
        # Hot path: "list active refresh tokens for this user" (logout-all etc.).
        Index("ix_refresh_tokens_user_revoked", "user_id", "revoked_at"),
    )


# ---------------------------------------------------------------------------
# PasswordResetToken
# ---------------------------------------------------------------------------


class PasswordResetToken(Base):
    """
    Admin-initiated password reset token (Phase 4 PR #13).

    The plaintext reset token (`secrets.token_urlsafe(32)`) is generated by the
    admin endpoint, hashed with bcrypt, and persisted as `token_hash`. The
    plaintext is intentionally discarded after issuance — Phase 6 PR #18 will
    wire the email channel that delivers it to the user, and the consumer
    endpoint (`POST /auth/password-reset/confirm`) will bcrypt-verify the
    plaintext supplied by the user against the stored hash.

    Lifecycle:
      - issued     : insert a new row with `used_at IS NULL`, `invalidated_at IS NULL`
      - superseded : a newer issuance for the same user marks earlier rows
                     `invalidated_at = now()` (single-pending-token policy)
      - consumed   : the confirm endpoint sets `used_at = now()` after a
                     successful bcrypt verify
      - expired    : `expires_at < now()`; rows are not auto-deleted (a Celery
                     Beat sweeper purges expired rows in a future PR)

    Security notes:
      - `token_hash` lives in `core.audit._SENSITIVE_COLUMNS` so the audit
        listener masks it to "***" in `audit_logs.diff`.
      - The 1-hour TTL is enforced in code at issuance time; the schema does
        not encode it so the admin can configure it later via env var.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )

    __table_args__ = (
        Index("ix_password_reset_tokens_user_id", "user_id"),
        Index("ix_password_reset_tokens_expires_at", "expires_at"),
    )


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """
    Immutable record of every mutation + auth event.

    Column names are part of the auth integration test contract
    (`tests/integration/test_auth_flow.py` reads `actor_user_id`, `target_table`,
    `action`, `created_at` via raw SQL — do not rename without updating the
    test).

    PII note: ip + user_agent are operational data. Retention is 90 days
    (Phase 5 will add a purge task). Passwords/tokens MUST never be written
    here — services are responsible for masking via core.logging.mask_pii().
    """

    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_table: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        # Time-range queries dominate (admin audit log views).
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_actor_user_id", "actor_user_id"),
        Index("ix_audit_logs_team_id", "team_id"),
        Index("ix_audit_logs_request_id", "request_id"),
        # Common compound: "show audit for this team in this window".
        Index("ix_audit_logs_team_created_at", "team_id", "created_at"),
        # JSONB GIN for "find audits whose diff touched column X".
        Index("ix_audit_logs_diff_gin", "diff", postgresql_using="gin"),
        # Phase 4 PR #14 — admin Audit Log search filters by target_table /
        # action (whitelisted enum strings) and the compound covers the
        # default admin query "audit rows for table X by user Y newest first".
        Index("ix_audit_logs_target_table", "target_table"),
        Index("ix_audit_logs_action", "action"),
        Index(
            "ix_audit_logs_target_actor_created",
            "target_table",
            "actor_user_id",
            "created_at",
        ),
    )
