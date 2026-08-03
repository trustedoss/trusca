"""notifications + notification_preferences — in-app notification center

Revision ID: 0011
Revises: 0010
Created: 2026-05-09

Phase: 6 (chore A2)
PR: chore/phase6-inapp-notifications
Kind: schema
Forward-only: yes

What:
  - Create Postgres ENUM type ``notification_kind`` with values:
      ('scan_completed', 'scan_failed', 'cve_detected', 'license_violation',
       'approval_pending', 'policy_gate_failed')
  - Create table ``notifications``::
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid()
        user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
        kind          notification_kind NOT NULL
        title         VARCHAR(256) NOT NULL
        body          VARCHAR(1024) NOT NULL
        link          VARCHAR(512) NULL
        target_table  VARCHAR(64) NULL
        target_id     UUID NULL
        read_at       TIMESTAMPTZ NULL
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()

      Indexes:
        ix_notifications_user_unread_created  (user_id, read_at,
                                               created_at DESC)
        ix_notifications_user_created         (user_id, created_at DESC)

      The first compound carries unread-first semantics: Postgres' default
      ASC ordering on ``read_at`` puts NULL (unread) ahead of any non-null
      (read) value, so a single index scan satisfies the "show me my unread
      first" UI path.

  - Create table ``notification_preferences``::
        user_id          UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE
        email_enabled    BOOLEAN NOT NULL DEFAULT true
        slack_enabled    BOOLEAN NOT NULL DEFAULT false
        teams_enabled    BOOLEAN NOT NULL DEFAULT false
        in_app_enabled   BOOLEAN NOT NULL DEFAULT true
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()

      No additional indexes — ``user_id`` PK is the only access path.

Why:
  - Chore A2 backlog (post Phase 6 PR #22) introduces the in-app notification
    center. Outbound email/slack/teams already ships via PR #22's dispatcher;
    A2 adds the persistence + REST surface so the SPA can render the bell
    badge and the notification drawer.
  - We use a native Postgres ENUM (not VARCHAR + CHECK) because the kind set
    is closed and exhaustive at the application layer; ``ALTER TYPE
    notification_kind ADD VALUE`` is the documented forward-only path for
    new kinds.
  - ``target_table`` / ``target_id`` are deliberately untyped (no FK): a
    notification may reference a project, a scan, a component, or any other
    domain row. We do NOT want a CASCADE that erases notification history
    when (e.g.) a project is archived.
  - The composite index ``(user_id, read_at, created_at DESC)`` lets the
    most common UI query — "give me my unread, newest first" — be served
    by a single index scan with no temp sort, even at hundreds of thousands
    of rows.
  - Defaults match the API contract: ``email_enabled=true``,
    ``in_app_enabled=true``, slack/teams off (they are deployment-level
    integrations and most installs do not configure them).

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``.
  - The ENUM type is created with a plain ``CREATE TYPE`` statement to
    mirror prior migrations (0003, 0008, 0010); the model side binds with
    ``create_type=False`` so SQLAlchemy never emits a duplicate.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
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
    # 1. Postgres ENUM type
    # ------------------------------------------------------------------
    op.execute(
        "CREATE TYPE notification_kind AS ENUM ("
        "'scan_completed', 'scan_failed', 'cve_detected', "
        "'license_violation', 'approval_pending', 'policy_gate_failed'"
        ")"
    )

    notification_kind_col_type = postgresql.ENUM(
        "scan_completed",
        "scan_failed",
        "cve_detected",
        "license_violation",
        "approval_pending",
        "policy_gate_failed",
        name="notification_kind",
        create_type=False,
    )

    # ------------------------------------------------------------------
    # 2. notifications table
    # ------------------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("user_id", UUID_PK, nullable=False),
        sa.Column("kind", notification_kind_col_type, nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("body", sa.String(length=1024), nullable=False),
        sa.Column("link", sa.String(length=512), nullable=True),
        sa.Column("target_table", sa.String(length=64), nullable=True),
        sa.Column("target_id", UUID_PK, nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notifications_user_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_notifications_user_unread_created",
        "notifications",
        ["user_id", "read_at", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", sa.text("created_at DESC")],
    )

    # ------------------------------------------------------------------
    # 3. notification_preferences table
    # ------------------------------------------------------------------
    op.create_table(
        "notification_preferences",
        sa.Column("user_id", UUID_PK, primary_key=True),
        sa.Column(
            "email_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "slack_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "teams_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "in_app_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notification_preferences_user_id",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
