"""
GitHub App credential + installation models — v2.2-b1.

Tables:
  - ``github_app_credentials``    — per-team GitHub App registration. Stores the
                                    App's reversibly-encrypted PEM private key
                                    (Fernet ciphertext) + optional webhook secret.
  - ``github_app_installations``  — installations of that App on accounts / repos,
                                    each optionally opted-in to a single project.

Conventions (CLAUDE.md core rules + existing model files — mirrors
``models/api_key.py`` and ``models/scan.py::Project``):
  - PostgreSQL only. UUID PKs default to ``gen_random_uuid()`` (pgcrypto).
  - TIMESTAMPTZ for every timestamp; ``created_at`` / ``updated_at`` on every
    mutable row, ``server_default now()``.
  - Every FK column gets an explicit Index — Postgres does not auto-create them.
  - No environment access at import time (CLAUDE.md core rule #11).
  - Cross-domain relationships are one-way (github_app → auth / scan). We do NOT
    add ORM back-refs into ``models/auth.py`` or ``models/scan.py``.

Security contract for ``github_app_credentials`` (this is the b1 raison d'être):
  - The GitHub App PEM private key is a REVERSIBLE secret (we must recover it to
    mint installation tokens — unlike an API key, which is bcrypt-hashed and only
    ever verified). It is stored as a Fernet ciphertext in
    ``private_key_encrypted`` (NOT NULL). The plaintext PEM is NEVER persisted.
    Encryption / decryption lives in ``core.crypto``.
  - ``webhook_secret_encrypted`` is the App's webhook HMAC secret, also Fernet-
    encrypted, nullable (an App may be registered before its webhook is wired).
  - ``core.audit._SENSITIVE_COLUMNS`` masks ``private_key_encrypted`` and
    ``webhook_secret_encrypted`` to ``"***"`` in the audit diff — defence in
    depth so a soft-revoke / re-register UPDATE can never write ciphertext into
    ``audit_logs.diff``.
  - Soft-delete: revocation flips ``revoked_at`` / ``revoked_by_user_id`` rather
    than DELETE so the audit trail referencing the credential by id stays intact
    (mirrors ``api_keys``).
  - ``UniqueConstraint(team_id, app_id)`` — a team registers a given GitHub App
    once. (A re-register after revoke is a NEW row; we do not resurrect the old.)

``github_app_installations``:
  - ``installation_id`` is GitHub's per-installation id (string — GitHub returns
    a large integer but we store it as text to avoid bigint overflow assumptions
    and to keep the value-space opaque).
  - ``project_id`` is the OPT-IN link: a team explicitly attaches an installation
    (account/repo) to a single TrustedOSS project. ``ON DELETE SET NULL`` so
    deleting a project does not cascade-delete the installation row (the App is
    still installed on GitHub's side — only the opt-in link is severed).
  - ``UniqueConstraint(credential_id, installation_id, repository_full_name)``
    makes re-linking the same (installation, repo) idempotent. ``repository_full_name``
    is NULL for an account-wide (all-repos) installation; the unique constraint
    treats NULL repo as a distinct slot per Postgres NULL semantics, which is the
    intended "one account-wide link" behaviour (the service additionally guards
    idempotency on the NULL-repo path).
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from . import Base

# ---------------------------------------------------------------------------
# Helpers (mirror models/api_key.py + models/scan.py)
# ---------------------------------------------------------------------------

UUID_PK = UUID(as_uuid=True)
GEN_UUID = text("gen_random_uuid()")
NOW = text("now()")


# ---------------------------------------------------------------------------
# GitHubAppCredential
# ---------------------------------------------------------------------------


class GitHubAppCredential(Base):
    """A team's registered GitHub App + its encrypted credentials.

    Team-scoped (mirrors ``api_keys``): every credential belongs to exactly one
    team and is managed by that team's admins (or super_admin). The PEM private
    key is stored Fernet-encrypted and recovered only inside the token-minting
    path (``services.github_app_service.mint_installation_token``).
    """

    __tablename__ = "github_app_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )

    # GitHub App numeric id (stored as text — opaque value-space, no bigint
    # overflow assumptions). Required to build the App JWT ``iss`` claim.
    app_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Human-readable App slug (e.g. "trustedoss-scanner"). Optional metadata.
    app_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Fernet ciphertext of the App PEM private key. NEVER the plaintext.
    # Masked in the audit diff via core.audit._SENSITIVE_COLUMNS.
    private_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Fernet ciphertext of the webhook HMAC secret (optional — App may be
    # registered before its webhook is configured). Masked in the audit diff.
    webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW,
        onupdate=NOW,
    )

    # Revocation (soft-delete) — mirrors api_keys.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # A team registers a given GitHub App once.
        UniqueConstraint("team_id", "app_id", name="uq_github_app_credentials_team_app"),
        Index("ix_github_app_credentials_team_id", "team_id"),
        Index("ix_github_app_credentials_created_by_user_id", "created_by_user_id"),
        # Hot path: "list this team's live credentials" — partial index dodges
        # the soft-revoked rows.
        Index(
            "ix_github_app_credentials_active",
            "team_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


# ---------------------------------------------------------------------------
# GitHubAppInstallation
# ---------------------------------------------------------------------------


class GitHubAppInstallation(Base):
    """An installation of a team's GitHub App, optionally opted-in to a project.

    The ``project_id`` opt-in is the gate that b3's auto-PR flow will consult:
    a credential alone does NOT grant TrustedOSS the right to touch a project's
    repo — a team must explicitly link an installation to a project first.
    """

    __tablename__ = "github_app_installations"

    id: Mapped[uuid.UUID] = mapped_column(UUID_PK, primary_key=True, server_default=GEN_UUID)

    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID_PK,
        ForeignKey("github_app_credentials.id", ondelete="CASCADE"),
        nullable=False,
    )

    # GitHub's per-installation id (string — see module docstring).
    installation_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Account the App is installed on (org / user login). Optional metadata.
    account_login: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Repo the installation targets, "owner/name". NULL = account-wide install.
    repository_full_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # OPT-IN link to a single TrustedOSS project. SET NULL on project delete so
    # the installation row survives (only the link is severed).
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_PK,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=NOW,
        onupdate=NOW,
    )

    __table_args__ = (
        # Re-linking the same (installation, repo) under a credential is
        # idempotent. NULL repo (account-wide) is a distinct slot per PG NULL
        # semantics; the service guards the NULL-repo idempotency explicitly.
        UniqueConstraint(
            "credential_id",
            "installation_id",
            "repository_full_name",
            name="uq_github_app_installations_cred_inst_repo",
        ),
        Index("ix_github_app_installations_credential_id", "credential_id"),
        Index("ix_github_app_installations_project_id", "project_id"),
        Index("ix_github_app_installations_created_by_user_id", "created_by_user_id"),
    )


__all__ = [
    "GitHubAppCredential",
    "GitHubAppInstallation",
]
