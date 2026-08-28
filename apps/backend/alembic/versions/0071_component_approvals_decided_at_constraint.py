# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""component_approvals: require decided_at on terminal rows

Revision ID: 0071
Revises: 0070
Create Date: 2026-08-28

Kind: schema (CHECK constraint) + defensive data backfill, no shape change
Forward-only: yes

What:
  Adds ``ck_component_approvals_decided_at``: a terminal row (``status`` in
  ``approved``/``rejected``) must carry a non-null ``decided_at``. Mirrors
  ``ck_org_component_verdicts_decided_at`` (migration 0059), which added the
  same constraint for ``organization_component_verdicts`` at table-creation
  time. ``component_approvals`` predates that table and already had rows, so
  it could not get the constraint the same way.

Why:
  ``component_approval_service.resolve_for_project`` orders terminal rows by
  ``decided_at``, newest first, the same ordering dependency migration 0059's
  constraint protects on the other table. A null sorts last in that ordering,
  so a terminal row without a timestamp would lose to an older decision and
  the project's real answer would go unread: a rejected component reading as
  approved, with nothing failing loudly (issue #169).

  Nothing writes such a row today: ``component_approval_service.
  transition_approval`` and ``scripts.seed_demo`` both set ``decided_at`` in
  the same write that sets a terminal ``status``. The constraint is
  preventive, closing the gap before a future backfill or data migration
  introduces one silently, exactly as 0059's comment already states for its
  own table.

Data step:
  A backfill runs before the constraint is added, per CLAUDE.md's migration
  policy of separating schema from data even when no violation is expected:
  any terminal row missing ``decided_at`` gets it set to ``created_at``
  (``component_approvals`` has no ``updated_at`` column, so this is the best
  available evidence of when the row was written) rather than ``now()``,
  which would fabricate a decision time with no relation to when the row
  actually changed. Idempotent no-op today, but it means the constraint can
  never fail to apply because of a row this migration itself could have
  fixed.

Reversal:
  Forward-only. Dropping the constraint would be a plain
  ``ALTER TABLE ... DROP CONSTRAINT`` if ever needed, but no downgrade path is
  provided (CLAUDE.md §6).
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE component_approvals "
            "SET decided_at = created_at "
            "WHERE status IN ('approved', 'rejected') AND decided_at IS NULL"
        )
    )
    op.create_check_constraint(
        "ck_component_approvals_decided_at",
        "component_approvals",
        "status NOT IN ('approved', 'rejected') OR decided_at IS NOT NULL",
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0071 is forward-only")
