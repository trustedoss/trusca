"""notification kind — vuln_sla_breach (X1 SLA/aging, backend step 2)

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-25

Phase: X1 vulnerability SLA/aging tracking, backend step 2 (breach alerts)
Kind: schema (additive — one new enum value; no data migration)
Forward-only: yes

What:
  - Add ``vuln_sla_breach`` to the ``notification_kind`` native PG ENUM.
    Emitted by the daily SLA sweep beat when an open finding's SLA due date
    (first_detected_at + severity policy days, see
    ``core.config.vuln_sla_days``) has just been crossed.

Why:
  - The in-app notification row INSERT validates against the ENUM; without
    this value the sweep task's ``create_notification_sync`` would raise.
    Same single-value expand as migration 0030 (approval_state_changed).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG 12+ permits ALTER TYPE ... ADD VALUE inside a transaction (the new
    # value just cannot be USED in the same transaction — we only add it here).
    # IF NOT EXISTS keeps the migration idempotent across partial re-runs.
    op.execute(
        "ALTER TYPE notification_kind ADD VALUE IF NOT EXISTS 'vuln_sla_breach'"
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6). Removing an enum value requires a type
    # rebuild and risks orphaning rows; we never drop emitted kinds.
    pass
