"""password_reset_tokens — admin-initiated password reset

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-07

Phase: 4 (PR #13 — Admin Users management)
PR: #13
Kind: schema
Forward-only: yes

What:
  - Create the ``password_reset_tokens`` table::
      id              UUID PRIMARY KEY DEFAULT gen_random_uuid()
      user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE
      token_hash      VARCHAR(128) NOT NULL    -- bcrypt hash of the urlsafe token
      expires_at      TIMESTAMPTZ NOT NULL
      used_at         TIMESTAMPTZ NULL
      invalidated_at  TIMESTAMPTZ NULL
      created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
  - Add ``ix_password_reset_tokens_user_id`` for "issue a new token, invalidate
    pending tokens for the same user" lookups.
  - Add ``ix_password_reset_tokens_expires_at`` for a future TTL sweeper
    (Celery Beat) that prunes expired rows in O(log n).

Why:
  - Phase 4 PR #13 ``POST /v1/admin/users/{id}/password-reset`` issues a
    one-shot password reset token. The plaintext token never touches the
    database — we persist a bcrypt hash so even a database leak does not
    yield reusable tokens (CLAUDE.md §3 "비밀번호 평문 X, bcrypt hash 만").
  - Single-pending-token policy: when a new token is issued for a user the
    service marks any previous unused/non-expired/non-invalidated rows as
    ``invalidated_at = now()``. The ``user_id`` index keeps that lookup cheap.
  - Phase 6 PR #18 will wire the email channel and the corresponding
    ``POST /auth/password-reset/confirm`` endpoint that bcrypt-verifies the
    plaintext supplied by the user against the stored hash. The schema
    ships now so the model + listener + audit trail are in place when the
    consumer endpoint lands.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``. Dropping the table is a manual / scripted op.
  - The bcrypt hash column is 128 chars to mirror ``refresh_tokens.token_hash``.
    bcrypt actually produces ~60-char strings; the extra room is harmless.
  - This table IS audited (no entry in ``core.audit._NON_AUDITED_TABLES``).
    Issuing a reset token therefore emits a single ``audit_logs`` row with
    ``target_table='password_reset_tokens'`` and ``action='create'``. The
    listener auto-masks the ``token_hash`` column to ``"***"`` (it's in
    ``core.audit._SENSITIVE_COLUMNS``), so the audit row records the
    issuance without leaking the hash itself.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UUID_PK = postgresql.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("user_id", UUID_PK, nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_password_reset_tokens_user_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_password_reset_tokens_user_id",
        "password_reset_tokens",
        ["user_id"],
    )
    op.create_index(
        "ix_password_reset_tokens_expires_at",
        "password_reset_tokens",
        ["expires_at"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
