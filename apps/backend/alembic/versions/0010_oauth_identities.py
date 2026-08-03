"""oauth_identities — external OAuth (GitHub / Google) account links

Revision ID: 0010
Revises: 0009
Created: 2026-05-09

Phase: 8
PR: #23
Kind: schema
Forward-only: yes

What:
  - Create Postgres ENUM type ``oauth_provider`` ('github', 'google').
  - Create table ``oauth_identities``::
        id                   UUID PRIMARY KEY DEFAULT gen_random_uuid()
        user_id              UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
        provider             oauth_provider NOT NULL
        provider_user_id     VARCHAR(128) NOT NULL  -- GitHub: numeric id,
                                                    -- Google: 'sub' claim
        email                VARCHAR(255) NOT NULL  -- per-provider verified email,
                                                    -- audit/recovery only
        avatar_url           TEXT NULL
        linked_at            TIMESTAMPTZ NOT NULL DEFAULT now()
        last_login_at        TIMESTAMPTZ NULL

      Indexes / constraints:
        uq_oauth_identities_provider_pid  UNIQUE (provider, provider_user_id)
        ix_oauth_identities_user_id       (user_id)
        ix_oauth_identities_email         (email)

Why:
  - Phase 8 PR #23 introduces the OAuth login surface for the demo SaaS
    (CLAUDE.md "데모 SaaS: 가입 시 개인 Team 자동 생성"). Each external
    account is a SEPARATE row so a single user can link both GitHub and
    Google over time without conflating identities.
  - The unique ``(provider, provider_user_id)`` constraint prevents account
    takeover: a second User cannot claim the same external account because
    the INSERT collides at the DB layer. The service treats the
    ``IntegrityError`` as the canonical "this external identity already
    exists" signal — there is NO SELECT-then-INSERT (TOCTOU race).
  - We persist ``email`` per-row even though the User table also has one,
    because providers can return an email distinct from the User's primary
    email (e.g. GitHub no-reply addresses). Investigators rely on the
    per-identity email to correlate forensic data with provider audit logs.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
  - The table has no ``updated_at`` — rows are effectively append-once
    (only ``last_login_at`` mutates), which mirrors the audit-friendly
    immutability of refresh_tokens.
  - ``email`` is plain VARCHAR (not CITEXT) because it is metadata for
    audit/recovery, not a uniqueness key. Lookups use the unique
    ``(provider, provider_user_id)`` index instead.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
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
    # Plain CREATE TYPE — consistent with prior migrations (0003, 0008).
    # The migration is forward-only and runs exactly once per DB; the
    # SQLAlchemy model binds with create_type=False so the ORM never
    # emits a duplicate CREATE TYPE.
    op.execute("CREATE TYPE oauth_provider AS ENUM ('github', 'google')")

    # ------------------------------------------------------------------
    # 2. Main table
    # ------------------------------------------------------------------
    # Use postgresql.ENUM(..., create_type=False) so SQLAlchemy's
    # before_create event does NOT attempt a second CREATE TYPE.
    oauth_provider_col_type = postgresql.ENUM(
        "github",
        "google",
        name="oauth_provider",
        create_type=False,
    )

    op.create_table(
        "oauth_identities",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("user_id", UUID_PK, nullable=False),
        sa.Column("provider", oauth_provider_col_type, nullable=False),
        sa.Column("provider_user_id", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("avatar_url", sa.Text, nullable=True),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_oauth_identities_user_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_user_id",
            name="uq_oauth_identities_provider_pid",
        ),
    )

    # ------------------------------------------------------------------
    # 3. Indexes
    # ------------------------------------------------------------------
    # FK indexes (Postgres does not auto-create them).
    op.create_index(
        "ix_oauth_identities_user_id",
        "oauth_identities",
        ["user_id"],
    )
    # Lookup by provider-side email (audit / "find the linked account
    # for a given email" admin queries).
    op.create_index(
        "ix_oauth_identities_email",
        "oauth_identities",
        ["email"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
