# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""oauth_provider += oidc

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-17

Phase: single sign-on (N6)
Kind: schema (additive: one enum value; no data migration)
Forward-only: yes

What:
  Add ``oidc`` to the ``oauth_provider`` Postgres enum.

Why:
  ``oauth_identities.provider`` is a native enum created by 0010 with exactly
  two values. The generic OpenID Connect provider writes a third, so without
  this the feature is not merely unsupported: the sign-in completes at the
  identity provider, the callback runs its first query against the identities
  table, and Postgres rejects the value. The user has already consented and
  spent the authorisation code by then, and the error arrives as a 500 rather
  than the documented redirect.

  The provider list the login page reads is a separate constant, so the button
  renders and reports itself configured regardless of this type. Nothing fails
  until a real sign-in is attempted.

Reversal:
  Forward-only, like every other value we have added. Removing an enum value
  needs a type rebuild and would orphan any identity row already written.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0056"
down_revision: str | None = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE oauth_provider ADD VALUE IF NOT EXISTS 'oidc'")


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    pass
