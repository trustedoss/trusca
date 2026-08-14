# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""record why a webhook delivery went unscanned

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-14

Phase: webhook observability (gap #39)
Kind: schema (additive: one nullable column, one index; no data migration)
Forward-only: yes

What:
  ``webhook_deliveries.outcome``: how the delivery ended, one of the statuses
  the receiver already reports on the wire (``enqueued``, ``duplicate``,
  ``ignored``, ``skipped_active_scan``, ``skipped_team_at_capacity``,
  ``skipped_disk_full``). Nullable.

  ``ix_webhook_deliveries_outcome_received`` on ``(outcome, received_at)``,
  which is the shape of the question the column exists to answer.

Why a column rather than a second table:
  The alternative was to keep ``webhook_deliveries`` purely an idempotency key
  store and record outcomes beside it. That splits one delivery across two
  tables, and every "why did this push go unscanned" query then starts with a
  join. The delivery row already carries provider, event type and project; the
  ending belongs with them.

Why NULL is meaningful:
  A row written before this column existed. Not "unknown outcome" in the sense
  of something having gone wrong: those deliveries ended in one of the four
  ways, the row just did not record which.

Backfill:
  None. ``enqueued_scan_id IS NOT NULL`` on an old row does imply ``enqueued``,
  but the three NULL cases are exactly the ones that cannot be told apart in
  retrospect, and a partial backfill would make the column look more complete
  than it is.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column("outcome", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_webhook_deliveries_outcome_received",
        "webhook_deliveries",
        ["outcome", "received_at"],
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("downgrade is not supported")
