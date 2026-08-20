# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""audit_export_cursors

Revision ID: 0066
Revises: 0065
Create Date: 2026-08-20

Phase: shipping the audit trail somewhere else, continuously (N17)
Kind: schema (additive: one table; no data migration)
Forward-only: yes

What:
  Where an export got to. One row per destination, holding the position of
  the last audit row handed over.

Why:
  A deployment that collects its logs centrally wants the audit trail there
  too, and wants it without a person running an export by hand every morning.
  That needs somewhere to remember what has already been sent.

  Deliberately not a column on ``audit_logs``. That table is append-only and
  a trigger enforces it (revision 0012), so an ``exported_at`` there would
  either mean dropping the trigger or carving an exception into it, and the
  exception is the whole property. The position lives in its own table where
  updating it is ordinary.

Reversal:
  Forward-only, and additive. With no destination configured nothing writes
  here and nothing reads it, and the audit API answers exactly as before.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_export_cursors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # One cursor per destination. A deployment that changes its collector
        # address starts a new row rather than inheriting a position that
        # describes what the old collector already has.
        sa.Column("destination", sa.String(length=255), nullable=False, unique=True),
        # The position, as the pair the export orders by. A timestamp alone
        # cannot resume safely: audit rows share a millisecond often enough
        # that "everything after this instant" either repeats the tail of a
        # batch or skips it.
        sa.Column("last_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rows_exported", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "(last_created_at IS NULL) = (last_id IS NULL)",
            name="ck_audit_export_cursors_position_is_whole",
        ),
    )


def downgrade() -> None:
    """Forward-only (CLAUDE.md 마이그레이션 정책)."""
    raise NotImplementedError("0066 is forward-only")
