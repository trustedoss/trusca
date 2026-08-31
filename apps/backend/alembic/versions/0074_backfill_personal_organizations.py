# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""organizations: backfill is_personal on pre-existing signup-created rows

Revision ID: 0074
Revises: 0073
Create Date: 2026-08-30

Kind: data (idempotent UPDATE), no schema change
Forward-only: yes

What:
  Sets ``is_personal = true`` on every existing ``organizations`` row whose
  ``slug`` matches ``^org-[0-9a-f]{12}$`` -- the exact, and only, shape both
  signup paths generate (``services/auth_service.py``'s
  ``_register_creates_team`` and ``services/oauth_service.py``'s
  ``_create_user_with_personal_team``, both ``slug=f"org-{user.id.hex[:12]}"``).

Why:
  Security review, self-resource-validation-plan-2026-08-30.md §6-5, second
  pass. Migration 0073 added the ``is_personal`` column with every existing
  row defaulting to ``false``, reasoned as "preserves today's behaviour" --
  true for a fresh deployment, but wrong for any deployment that was already
  running with self-registration open before 0073 landed: a personal
  organization created by a real signup, before this column existed, would
  otherwise be permanently mislabelled as a platform organization and
  ``_pick_default_org`` would go on auto-picking it, silently reproducing
  the exact cross-tenant exposure this column exists to close. This closes
  that gap for data that already exists rather than only for rows created
  after 0073.

  The regex is deliberately narrow (exactly ``org-`` + 12 lowercase hex
  chars, matching a 48-bit slice of a UUID4) so it cannot misclassify a
  platform organization an installer or admin named some other way (the
  bootstrap script uses ``default``, the demo seed uses a fixed slug, and an
  operator-chosen name is very unlikely to coincidentally match this exact
  shape) as personal.

Idempotency:
  A plain ``UPDATE ... WHERE is_personal = false AND slug ~ '...'``. Running
  it twice changes nothing the second time (no rows still match the WHERE
  clause with ``is_personal`` already true), and it is safe to run against a
  database where migration 0073 has not left any qualifying row (a no-op).

Reversal:
  Forward-only. There is no way to distinguish "was already false" from
  "flipped false by this migration" after the fact, so a downgrade cannot
  restore the pre-migration state; none is provided (CLAUDE.md §6).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0074"
down_revision: str | None = "0073"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE organizations "
            "SET is_personal = true "
            "WHERE is_personal = false AND slug ~ '^org-[0-9a-f]{12}$'"
        )
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0074 is forward-only")
