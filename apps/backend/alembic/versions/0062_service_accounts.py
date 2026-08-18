# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""users: service accounts

Revision ID: 0062
Revises: 0061
Create Date: 2026-08-19

Phase: automation credentials that do not die with a person (N13)
Kind: schema (additive: two columns, one partial index; no data migration)
Forward-only: yes

What:
  ``is_service_account``: this row is an automation identity, not a person.
  ``managed_by_user_id``: the person answerable for it. Recorded for
  accountability and transferable; never consulted when authenticating.

Why:
  An API key stops working when the person who issued it is deactivated. That
  is the right rule for a person's key and the wrong one for a pipeline's: a
  build that has run nightly for a year stops the day its author leaves, and
  the first anyone hears of it is a red pipeline.

  A row in ``users`` rather than a table of its own. Twenty-six foreign keys
  across ten modules point at ``users.id``, and the whole permission model
  stands on ``Membership(user_id, team_id, role)``. A parallel identity would
  need a parallel membership table, a branch in the principal synthesis, and a
  second kind of audit actor; every one of those is a place for the two to
  drift. Sharing the table means the key-lifetime rule needs no branch at all:
  the auth path still asks only whether the issuer is active, and for a
  service account the issuer is the service account.

  The cost is that these rows must be kept off every surface built for people,
  which is the one real risk in this design and is asserted directly.

Reversal:
  Forward-only, and inert until something sets the flag. No existing row is
  touched: ``is_service_account`` backfills false, so every account stays a
  person and every existing key keeps the lifetime rule it has today.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_service_account",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "managed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    # Partial: service accounts are a handful beside the people, and the
    # queries that want them always want only them.
    op.create_index(
        "ix_users_service_accounts",
        "users",
        ["is_service_account"],
        postgresql_where=sa.text("is_service_account"),
    )
    op.create_index(
        "ix_users_managed_by_user_id",
        "users",
        ["managed_by_user_id"],
        postgresql_where=sa.text("managed_by_user_id IS NOT NULL"),
    )
    # A person cannot have a steward, and a service account cannot steward
    # anything. Stated in the schema because the second half is what stops a
    # chain of service accounts vouching for each other with no person at the
    # end of it.
    op.create_check_constraint(
        "ck_users_steward_only_for_service_accounts",
        "users",
        "managed_by_user_id IS NULL OR is_service_account",
    )
    # An automation identity is never a deployment administrator. Stated in the
    # schema rather than only in the code that creates them, because the create
    # path is not the only writer of ``is_superuser``: the admin role endpoint
    # writes it too, and an escalation there would produce a non-expiring
    # org-wide key whose issuer outlives every session that made it.
    op.create_check_constraint(
        "ck_users_service_account_not_superuser",
        "users",
        "NOT (is_service_account AND is_superuser)",
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
