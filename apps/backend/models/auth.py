# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""
Auth domain models — Phase 1 PR #5.

Tables: organizations, teams, users, memberships, refresh_tokens, audit_logs.

Conventions (CLAUDE.md core rules):
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
    BigInteger,
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
# Order mirrors the Postgres type, which lists values in the order they were
# added: `viewer` came last (migration 0055) even though it is the lowest
# grade. Privilege order lives in ``core.security._ROLE_PRIORITY``, not here.
ROLE_VALUES = ("super_admin", "team_admin", "developer", "viewer")


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
    # True for an org created as a side effect of a signup (self-registration
    # or OAuth), one per user, for tenant isolation of org-scoped data. False
    # for the shared/platform org an installer or admin provisions (security
    # review, self-resource-validation-plan-2026-08-30.md §6-5:
    # _pick_default_org must never auto-attach a new admin-created team to a
    # stranger's personal org, which "exactly one org exists" alone cannot
    # rule out -- that lone org can itself be the first signup's personal
    # one). Existing rows backfill to False (platform org) on migration:
    # every pre-existing single-org deployment already relied on that row
    # being the shared one, so this preserves current behaviour rather than
    # guessing intent retroactively.
    is_personal: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=EMPTY_JSONB
    )
    #: IANA name the organization's calendar deadlines are read in (0083).
    #:
    #: A column rather than a ``settings`` key because a verdict reads it: a
    #: misspelt JSONB key returns NULL and silently falls back to UTC, and the
    #: only symptom would be western users going overdue hours early. The rule
    #: this follows is in 0083's docstring.
    #:
    #: Not constrained here: a CHECK cannot consult ``pg_timezone_names``.
    #: ``schemas.admin`` rejects names ``zoneinfo`` will not load, and
    #: ``services.due_date`` treats an unloadable stored value as UTC rather
    #: than raising in the middle of a sweep.
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="UTC"
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
    # Set by `set_password`, which is the only place the hash is rotated for an
    # existing user. Access tokens minted before this are refused, so changing
    # a password ends the sessions that were already open. NULL means the
    # password has not changed since the column was added (migration 0077):
    # the check is skipped, because backfilling it would have logged out every
    # user of a running deployment at upgrade time.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Second factor. The secret and the flag are separate on purpose: a secret
    # exists from the moment somebody scans the QR code, and enabling there
    # would lock out anyone who closed the tab before their app was working.
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The TOTP step most recently accepted. A code is valid for its whole step,
    # so this is what stops one being presented twice.
    mfa_last_counter: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Stamped when an administrator clears the second factor, so the sessions
    # open at that moment end: ``credential_change_invalidates`` refuses every
    # access token minted before it, and the clear revokes the refresh rows.
    #
    # NOT stamped when somebody finishes enrolling. The request that turns the
    # factor on carries a token minted before it, so stamping there signs that
    # person out on the recovery-code screen. Turning a factor on to evict a
    # session you do not trust would need a fresh token pair handed back in
    # the same response; until that exists the guide says to reset the
    # password for that case.
    #
    # Separate from password_changed_at rather than shared, so the two reasons
    # stay distinguishable.
    mfa_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # N13: this row is an automation identity rather than a person.
    #
    # It shares the table because the permission model stands on
    # ``Membership(user_id, team_id, role)`` and twenty-six foreign keys point
    # here; a parallel identity would need a parallel everything. The payoff is
    # that the key-lifetime rule needs no branch: the auth path still asks only
    # whether the issuer is active, and for one of these the issuer is itself,
    # so a person leaving does not stop a pipeline.
    #
    # The price is that these must be kept off every surface built for people,
    # which is asserted rather than assumed.
    is_service_account: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # The person answerable for a service account. Recorded so an unattended
    # credential still has a name against it, and transferable when they leave.
    # Never consulted when authenticating: coupling the key's life to a person
    # is the thing this exists to undo.
    managed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
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

    __table_args__ = (
        # A person has no steward, and a service account cannot be one. The
        # second half is what stops a chain of service accounts vouching for
        # each other with no person at the end of it.
        CheckConstraint(
            "managed_by_user_id IS NULL OR is_service_account",
            name="ck_users_steward_only_for_service_accounts",
        ),
        # An automation identity is never a deployment administrator. The
        # create path already refuses it, but that is not the only writer of
        # this column, and the escalation it would allow produces a key that
        # outlives every session involved in making it.
        CheckConstraint(
            "NOT (is_service_account AND is_superuser)",
            name="ck_users_service_account_not_superuser",
        ),
        Index(
            "ix_users_service_accounts",
            "is_service_account",
            postgresql_where=text("is_service_account"),
        ),
        Index(
            "ix_users_managed_by_user_id",
            "managed_by_user_id",
            postgresql_where=text("managed_by_user_id IS NOT NULL"),
        ),
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


class UserRecoveryCode(Base):
    """One single-use code, stored the way a password is.

    A spent row is kept rather than deleted so the account page can show what
    was used and when. Regenerating deletes the unused ones: a spent code is
    already refused, and leaving an unused one live would defeat the reason
    somebody regenerates.
    """

    __tablename__ = "user_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
        # ER12 (0078) - the finding history panel. Two queries per vulnerability
        # opened, both keyed on (target_table, target_id) and ordered by time.
        # ``ix_audit_logs_target_table`` alone leaves target_id to a heap
        # filter, and ``vulnerability_findings`` is the biggest bucket that
        # column has: the scan pipeline writes one create row per finding.
        Index(
            "ix_audit_logs_target_table_target_id_created_at",
            "target_table",
            "target_id",
            "created_at",
        ),
        # ER12 (0078) - the admin audit search filtered by actor. The
        # actor-only index above leaves the ordering to a sort, and under a
        # LIMIT the planner would rather walk the created_at index backwards
        # and discard everyone else's rows.
        Index("ix_audit_logs_actor_created_at", "actor_user_id", "created_at"),
    )
