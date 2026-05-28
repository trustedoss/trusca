"""api_keys + webhook_deliveries + projects.webhook_* — CI / Webhook surface

Revision ID: 0009
Revises: 0008
Created: 2026-05-08

Phase: 5
PR: #16
Kind: schema
Forward-only: yes

What:
  - Create table ``api_keys``::
        id                  UUID PK DEFAULT gen_random_uuid()
        key_prefix          VARCHAR(16) NOT NULL UNIQUE
        key_hash            VARCHAR(255) NOT NULL    -- bcrypt(plaintext)
        name                VARCHAR(100) NOT NULL
        scope               VARCHAR(16) NOT NULL     -- 'org'|'team'|'project'
        team_id             UUID NULL  → teams(id)    ON DELETE CASCADE
        project_id          UUID NULL  → projects(id) ON DELETE CASCADE
        created_by_user_id  UUID NULL  → users(id)    ON DELETE SET NULL
        created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        last_used_at        TIMESTAMPTZ NULL
        revoked_at          TIMESTAMPTZ NULL
        revoked_by_user_id  UUID NULL  → users(id)    ON DELETE SET NULL

      Constraints / indexes:
        ck_api_keys_scope_values        scope IN ('org','team','project')
        ck_api_keys_scope_consistency   org⇒both NULL, team⇒team_id only,
                                        project⇒project_id NOT NULL
        ix_api_keys_team_id
        ix_api_keys_project_id
        ix_api_keys_created_by_user_id
        ix_api_keys_active              partial index on key_prefix
                                        WHERE revoked_at IS NULL

  - Create table ``webhook_deliveries``::
        id                  UUID PK DEFAULT gen_random_uuid()
        provider            VARCHAR(16) NOT NULL     -- 'github'|'gitlab'
        delivery_id         VARCHAR(128) NOT NULL
        event_type          VARCHAR(64) NOT NULL
        payload_hash        VARCHAR(64) NOT NULL     -- sha256 hex
        received_at         TIMESTAMPTZ NOT NULL DEFAULT now()
        api_key_id          UUID NULL  → api_keys(id)  ON DELETE SET NULL
        project_id          UUID NULL  → projects(id)  ON DELETE SET NULL
        enqueued_scan_id    UUID NULL  → scans(id)     ON DELETE SET NULL

      Constraints / indexes:
        ck_webhook_deliveries_provider_values  provider IN ('github','gitlab')
        ix_webhook_deliveries_provider_id      UNIQUE (provider, delivery_id)
                                               -- canonical idempotency gate
        ix_webhook_deliveries_received_at
        ix_webhook_deliveries_api_key_id
        ix_webhook_deliveries_project_id
        ix_webhook_deliveries_enqueued_scan_id

  - Add nullable columns to ``projects``::
        webhook_secret    VARCHAR(64)  -- HMAC key (GitHub) / shared token (GitLab)
        webhook_provider  VARCHAR(16)  -- 'github' | 'gitlab'

Why:
  - Phase 5 PR #16 introduces the public REST + Webhook surface for CI / SCM
    integrations (docs/v2-execution-plan.md §3.7). API keys must NEVER be
    stored in plaintext (CLAUDE.md §3) — bcrypt cost-12 mirrors the user
    password policy. The 12-char ``key_prefix`` is the look-up key and is
    safe to display in management UIs (it carries no secret material).
  - The ``ck_api_keys_scope_consistency`` CHECK constraint pushes scope
    coherence into the database so a malformed INSERT cannot smuggle a
    project-scoped key that secretly grants org-wide reach. Service code
    treats a CHECK violation as an internal bug (5xx), not a user error.
  - The unique partial index ``ix_api_keys_active`` (key_prefix WHERE
    revoked_at IS NULL) makes the auth lookup a single index probe and
    silently dodges revoked rows — even a leaked-then-revoked key cannot
    re-authenticate.
  - The webhook unique index on ``(provider, delivery_id)`` is the
    canonical idempotency gate. The service attempts an INSERT; a duplicate
    retry collides with this index and the unique-violation drives the
    ``200 OK {"status":"duplicate"}`` path. There is NO SELECT-then-INSERT
    (TOCTOU race).
  - ``projects.webhook_secret`` is masked in audit_logs.diff via
    ``core.audit._SENSITIVE_COLUMNS`` (the new entry added in this PR),
    so a future ``UPDATE projects SET webhook_secret = ...`` row never
    leaks the plaintext into the audit table.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
  - Partial unique index on ``api_keys.key_prefix`` uses op.create_index with
    postgresql_where; this is supported by Alembic for simple predicates and
    is equivalent to the model's __table_args__ declaration so ``alembic
    check`` stays clean.
  - No data migration: tables are new and the project columns are nullable
    additions (existing rows get NULL, which the service treats as "webhooks
    disabled").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = postgresql.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. api_keys
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("team_id", UUID_PK, nullable=True),
        sa.Column("project_id", UUID_PK, nullable=True),
        sa.Column("created_by_user_id", UUID_PK, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by_user_id", UUID_PK, nullable=True),
        sa.UniqueConstraint("key_prefix", name="uq_api_keys_key_prefix"),
        sa.CheckConstraint(
            "scope IN ('org', 'team', 'project')",
            name="ck_api_keys_scope_values",
        ),
        sa.CheckConstraint(
            "("
            "  (scope = 'org'     AND team_id IS NULL AND project_id IS NULL)"
            "  OR (scope = 'team'    AND team_id IS NOT NULL AND project_id IS NULL)"
            "  OR (scope = 'project' AND project_id IS NOT NULL)"
            ")",
            name="ck_api_keys_scope_consistency",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_api_keys_team_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_api_keys_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_api_keys_created_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"],
            ["users.id"],
            name="fk_api_keys_revoked_by_user_id",
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_api_keys_team_id",
        "api_keys",
        ["team_id"],
    )
    op.create_index(
        "ix_api_keys_project_id",
        "api_keys",
        ["project_id"],
    )
    op.create_index(
        "ix_api_keys_created_by_user_id",
        "api_keys",
        ["created_by_user_id"],
    )
    # Partial index — auth hot path skips revoked rows.
    op.create_index(
        "ix_api_keys_active",
        "api_keys",
        ["key_prefix"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # 2. webhook_deliveries
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("delivery_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("api_key_id", UUID_PK, nullable=True),
        sa.Column("project_id", UUID_PK, nullable=True),
        sa.Column("enqueued_scan_id", UUID_PK, nullable=True),
        sa.CheckConstraint(
            "provider IN ('github', 'gitlab')",
            name="ck_webhook_deliveries_provider_values",
        ),
        sa.ForeignKeyConstraint(
            ["api_key_id"],
            ["api_keys.id"],
            name="fk_webhook_deliveries_api_key_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_webhook_deliveries_project_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["enqueued_scan_id"],
            ["scans.id"],
            name="fk_webhook_deliveries_enqueued_scan_id",
            ondelete="SET NULL",
        ),
    )

    op.create_index(
        "ix_webhook_deliveries_provider_id",
        "webhook_deliveries",
        ["provider", "delivery_id"],
        unique=True,
    )
    op.create_index(
        "ix_webhook_deliveries_received_at",
        "webhook_deliveries",
        ["received_at"],
    )
    op.create_index(
        "ix_webhook_deliveries_api_key_id",
        "webhook_deliveries",
        ["api_key_id"],
    )
    op.create_index(
        "ix_webhook_deliveries_project_id",
        "webhook_deliveries",
        ["project_id"],
    )
    op.create_index(
        "ix_webhook_deliveries_enqueued_scan_id",
        "webhook_deliveries",
        ["enqueued_scan_id"],
    )

    # ------------------------------------------------------------------
    # 3. projects.webhook_secret + webhook_provider
    # ------------------------------------------------------------------
    op.add_column(
        "projects",
        sa.Column("webhook_secret", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("webhook_provider", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
