# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""api_keys.permission_breadth

Revision ID: 0061
Revises: 0060
Create Date: 2026-08-18

Phase: an API key can be read-only (N7)
Kind: schema (additive: one NOT NULL column with a backfilling default)
Forward-only: yes

What:
  ``permission_breadth``: 'read_write' or 'read_only'. What a key may do,
  which is a different question from ``scope``: scope says which projects a
  key can reach, breadth says what it can do to them.

Why:
  Every key issued so far can trigger scans and push SBOMs, because there has
  never been a way to issue one that cannot. A pipeline that only reads scan
  results has to hold a key that could start one.

  The default here is 'read_write', and that is the whole care of this
  migration. Existing rows must keep exactly the breadth they have: a key
  backfilled as read-only would stop an external pipeline the next time it
  ran, with nothing in the portal to say why. New keys default to read-only,
  but that default lives in the issuance schema rather than the column, so the
  two cases cannot be confused for one another.

  The column default is then dropped in the same revision. It exists to
  backfill, which ADD COLUMN does in one statement; keeping it afterwards
  would let any later INSERT that omits the column mint a write-capable key.

Reversal:
  Forward-only. Until the auth path reads the column every key behaves as it
  does today, so reverting the code alone restores the previous system.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061"
down_revision: str | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column(
            "permission_breadth",
            sa.String(length=16),
            nullable=False,
            # Backfills every existing row with the breadth it already has.
            server_default=sa.text("'read_write'"),
        ),
    )
    # The default has done its work the moment the column exists: ADD COLUMN
    # ... DEFAULT backfills every existing row in that statement. Leaving it in
    # place afterwards would mean any later INSERT that omits the column mints
    # a write-capable key by accident, which is the confusion putting the safe
    # default in the schema layer was meant to remove. Dropped here so an
    # INSERT that forgets the column fails loudly instead.
    op.alter_column("api_keys", "permission_breadth", server_default=None)
    op.create_check_constraint(
        "ck_api_keys_permission_breadth",
        "api_keys",
        "permission_breadth IN ('read_write', 'read_only')",
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
