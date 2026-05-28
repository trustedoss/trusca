"""auth schema — organizations, teams, users, memberships, refresh tokens, audit logs

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-05

Phase: 1
PR: #5
Kind: schema
Forward-only: yes

What:
  - Enable the `citext` and `pgcrypto` Postgres extensions.
  - Create the `user_role` ENUM type ('super_admin','team_admin','developer').
  - Create tables: organizations, teams, users, memberships, refresh_tokens,
    audit_logs (in FK-dependency order).
  - Create explicit indexes on every FK column, plus the compound / GIN
    indexes called out in apps/backend/models/auth.py.

Why:
  - Phase 1 task 1.1 (`docs/v2-execution-plan.md` §3.2): authentication and
    RBAC are blockers for every other Phase 1 PR. The schema established here
    backs the JWT login + refresh rotation + audit pipeline that PRs #6-#9
    consume.
  - CITEXT on users.email lets us keep "Foo@Example.com" === "foo@example.com"
    at the DB layer without app-side normalization sprinkled everywhere.
  - JSONB + GIN on audit_logs.diff and organizations.settings is the standard
    "queryable JSON" path; we'll lean on it for admin dashboards in Phase 4+.

Notes:
  - First non-empty migration, so no expand/contract dance is needed yet.
  - Forward-only per CLAUDE.md §6: downgrade() raises NotImplementedError.
  - The `user_role` ENUM is created here directly; the model binds with
    `create_type=False` so SQLAlchemy never tries to re-create it on metadata
    emit.
  - PII retention (audit_logs.ip / user_agent) is handled by a Phase 5 purge
    task — schema only here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = postgresql.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
EMPTY_JSONB = sa.text("'{}'::jsonb")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions + ENUM type
    # ------------------------------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')  # gen_random_uuid()
    op.execute('CREATE EXTENSION IF NOT EXISTS "citext"')  # users.email
    op.execute(
        "CREATE TYPE user_role AS ENUM ('super_admin', 'team_admin', 'developer')"
    )

    user_role = postgresql.ENUM(
        "super_admin",
        "team_admin",
        "developer",
        name="user_role",
        create_type=False,
    )

    # ------------------------------------------------------------------
    # organizations
    # ------------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("name", name="uq_organizations_name"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index(
        "ix_organizations_settings_gin",
        "organizations",
        ["settings"],
        postgresql_using="gin",
    )

    # ------------------------------------------------------------------
    # teams
    # ------------------------------------------------------------------
    op.create_table(
        "teams",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("organization_id", UUID_PK, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_teams_organization_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "slug", name="uq_teams_org_slug"),
    )
    op.create_index("ix_teams_organization_id", "teams", ["organization_id"])

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_superuser", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # ------------------------------------------------------------------
    # memberships  (User × Team × Role)
    # ------------------------------------------------------------------
    op.create_table(
        "memberships",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("user_id", UUID_PK, nullable=False),
        sa.Column("team_id", UUID_PK, nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_memberships_team_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("user_id", "team_id", name="uq_memberships_user_team"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])
    op.create_index("ix_memberships_team_id", "memberships", ["team_id"])
    op.create_index("ix_memberships_team_role", "memberships", ["team_id", "role"])
    op.create_index("ix_memberships_user_role", "memberships", ["user_id", "role"])

    # ------------------------------------------------------------------
    # refresh_tokens  (rotation + reuse detection)
    # ------------------------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("user_id", UUID_PK, nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("parent_jti", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_refresh_tokens_user_id", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("jti", name="uq_refresh_tokens_jti"),
        sa.CheckConstraint(
            "revoked_reason IN ('rotated','logout','reuse_detected','expired')"
            " OR revoked_reason IS NULL",
            name="ck_refresh_tokens_revoked_reason",
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_parent_jti", "refresh_tokens", ["parent_jti"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    op.create_index(
        "ix_refresh_tokens_user_revoked",
        "refresh_tokens",
        ["user_id", "revoked_at"],
    )

    # ------------------------------------------------------------------
    # audit_logs
    # ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("actor_user_id", UUID_PK, nullable=True),
        sa.Column("team_id", UUID_PK, nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_table", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_audit_logs_team_id", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
    op.create_index("ix_audit_logs_actor_user_id", "audit_logs", ["actor_user_id"])
    op.create_index("ix_audit_logs_team_id", "audit_logs", ["team_id"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index(
        "ix_audit_logs_team_created_at", "audit_logs", ["team_id", "created_at"]
    )
    op.create_index(
        "ix_audit_logs_diff_gin", "audit_logs", ["diff"], postgresql_using="gin"
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
