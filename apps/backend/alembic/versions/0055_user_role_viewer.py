# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""user_role += viewer

Revision ID: 0055
Revises: 0054
Create Date: 2026-08-17

Phase: least-privilege role (N1)
Kind: schema (additive: one enum value; no data migration)
Forward-only: yes

What:
  Add ``viewer`` to the ``user_role`` Postgres enum.

Why:
  The role set has had one floor since Phase 1: a member is a developer or
  more. That floor decides more than it looks like it does, because the
  developer grade carries scan execution, every write, and the source tree
  together with ordinary reads. Anyone who only needs to look at findings
  (an auditor, a legal reviewer, a manager reading the portfolio) has to be
  given the grade that can also start scans and download the source.

  ``viewer`` is the read-only grade below developer. Its permissions land in
  the follow-up that rewires the gates; this revision only teaches the
  database the word, which has to happen first because the role column is a
  native enum and rejects anything the type does not list.

Assignment stays closed in this revision. The API schema does not accept the
new value yet, so no row can carry it until the gates know what it means and
a member holding it would otherwise reach nothing.

Reversal:
  Enum values cannot be dropped without rebuilding the type, so this is
  forward-only like every other value we have added. If the grade turns out
  to be wrong, leave the value in place, unused: removing it from the
  priority map in ``core.security`` makes the comparison treat it as
  privilege 0, which denies everything rather than granting anything.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG 12+ permits ALTER TYPE ... ADD VALUE inside a transaction (the new
    # value just cannot be USED in the same transaction, and nothing here
    # does). IF NOT EXISTS keeps this idempotent across partial re-runs.
    op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'viewer'")


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6). Dropping an enum value requires a type
    # rebuild and would orphan any row that had already been assigned it.
    pass
