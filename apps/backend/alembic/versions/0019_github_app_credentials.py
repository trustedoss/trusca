"""github app credentials — github_app_credentials + github_app_installations (v2.2 2.2-b1)

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-24

Phase: v2.2 (Track B — b1 "GitHub App credential storage + token-minting foundation")
PR: feat/v2.2-b1-github-app-creds
Kind: schema (additive — expand step; no data migration)
Forward-only: yes

What:
  - Create ``github_app_credentials``: per-team GitHub App registration holding
    the App's REVERSIBLY-encrypted PEM private key (Fernet ciphertext) and an
    optional encrypted webhook secret. Soft-delete (``revoked_at`` /
    ``revoked_by_user_id``) mirroring ``api_keys``.
  - Create ``github_app_installations``: installations of that App on accounts /
    repos, each optionally OPT-IN-linked to a single TrustedOSS project.

Why:
  - D1 chose a GitHub App (not a PAT) for the auto-PR remediation flow: per-repo
    fine-grained permissions, per-installation short-lived tokens, multi-tenant.
    b1 lays down the credential STORAGE + token-minting foundation; b2 (npm
    manifest adapter) and b3 (opt-in auto-PR) build on top. No PR-creation logic
    lands here.

Security / encryption-at-rest:
  - ``private_key_encrypted`` (NOT NULL) and ``webhook_secret_encrypted`` (NULL)
    store Fernet ciphertext only — the plaintext PEM / secret never touches the
    database. Encryption lives in ``core.crypto``; the audit listener masks both
    columns out of ``audit_logs.diff`` (``core.audit._SENSITIVE_COLUMNS``).
  - ``UniqueConstraint(team_id, app_id)`` — one registration of a given App per
    team. ``UniqueConstraint(credential_id, installation_id, repository_full_name)``
    makes re-linking an installation idempotent.
  - FK lifecycles: ``team_id`` ON DELETE CASCADE (a team's credentials die with
    the team); ``credential_id`` ON DELETE CASCADE (installations die with the
    credential); ``project_id`` ON DELETE SET NULL (deleting a project only
    severs the opt-in link, the GitHub-side installation persists);
    ``created_by_user_id`` / ``revoked_by_user_id`` ON DELETE SET NULL (keep rows
    queryable for audit after a user is deleted).

Notes:
  - **Expand step only** (CLAUDE.md §6 expand → migrate-data → contract),
    matching 0015/0016/0017/0018. Pure additive DDL (two CREATE TABLE +
    indexes). No raw SQL → no asyncpg ``::`` / TIMESTAMPTZ bind concerns.
  - ``gen_random_uuid()`` (pgcrypto) is already available from 0002.
  - Partial index ``ix_github_app_credentials_active`` (WHERE revoked_at IS NULL)
    bounds the "list this team's live credentials" hot path to the live set.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- github_app_credentials (new table) ---
    op.create_table(
        "github_app_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("app_id", sa.String(length=64), nullable=False),
        sa.Column("app_slug", sa.String(length=255), nullable=True),
        sa.Column("private_key_encrypted", sa.Text(), nullable=False),
        sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "team_id",
            "app_id",
            name="uq_github_app_credentials_team_app",
        ),
    )
    op.create_index(
        "ix_github_app_credentials_team_id",
        "github_app_credentials",
        ["team_id"],
    )
    op.create_index(
        "ix_github_app_credentials_created_by_user_id",
        "github_app_credentials",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_github_app_credentials_active",
        "github_app_credentials",
        ["team_id"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # --- github_app_installations (new table) ---
    op.create_table(
        "github_app_installations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "credential_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_app_credentials.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("installation_id", sa.String(length=64), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=True),
        sa.Column("repository_full_name", sa.String(length=512), nullable=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "credential_id",
            "installation_id",
            "repository_full_name",
            name="uq_github_app_installations_cred_inst_repo",
        ),
    )
    op.create_index(
        "ix_github_app_installations_credential_id",
        "github_app_installations",
        ["credential_id"],
    )
    op.create_index(
        "ix_github_app_installations_project_id",
        "github_app_installations",
        ["project_id"],
    )
    op.create_index(
        "ix_github_app_installations_created_by_user_id",
        "github_app_installations",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
