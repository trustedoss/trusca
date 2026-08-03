"""notification_kind += approval_state_changed (H-5)

Revision ID: 0030
Revises: 0029
Created: 2026-06-10

Phase: validation-remediation (H-5)
Kind: schema
Forward-only: yes

What:
  - Add ``approval_state_changed`` to the ``notification_kind`` Postgres enum.

Why:
  - The in-app notification catalog (``notification_kind`` enum / Pydantic
    Literal) and the external-dispatch catalog (``notifications.dispatcher``)
    had drifted: they shared only ``scan_completed``. When the approval
    disposition path is wired to notify the requester (this remediation), the
    Celery task writes an in-app row with kind ``approval_state_changed`` — the
    enum must accept it or the INSERT is rejected. Adding the value reconciles
    the two catalogs for the one kind that crosses both surfaces.

Backfill:
  - None. Additive enum value; existing rows unaffected.

Downgrade:
  - Forward-only per CLAUDE.md §6. Postgres cannot drop an enum value without a
    type rebuild; we never remove emitted kinds.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG 12+ permits ALTER TYPE ... ADD VALUE inside a transaction (the new
    # value just cannot be USED in the same transaction — we only add it here).
    # IF NOT EXISTS keeps the migration idempotent across partial re-runs.
    op.execute(
        "ALTER TYPE notification_kind ADD VALUE IF NOT EXISTS 'approval_state_changed'"
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6). Removing an enum value requires a type
    # rebuild and risks orphaning rows; we never drop emitted kinds.
    pass
