"""malicious_sync_state table + malicious_detected notification kind.

Two changes in one revision because they ship together and neither is useful
alone: the beat writes the status row, and the same beat raises the alert when
a refreshed snapshot flags something already in stock.

The ENUM value is added with ``ADD VALUE IF NOT EXISTS`` outside a transaction
block, the same shape as 0042 — PostgreSQL will not add an enum label inside a
transaction that later uses it, and Alembic's autocommit block is how the
other kind additions handled it.

Forward-only per CLAUDE.md §6.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "malicious_sync_state",
        sa.Column(
            "id", sa.Boolean(), primary_key=True, server_default=sa.text("true")
        ),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_result", sa.String(16), nullable=True),
        sa.Column("skipped_reason", sa.String(64), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=True),
        sa.Column("purl_count", sa.Integer(), nullable=True),
        sa.Column("ecosystems_ok", sa.Integer(), nullable=True),
        sa.Column("ecosystems_failed", sa.Integer(), nullable=True),
        sa.Column("stamped", sa.Integer(), nullable=True),
        sa.Column("flagged", sa.Integer(), nullable=True),
        sa.Column("newly_flagged", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id", name="ck_malicious_sync_state_singleton"),
    )

    # Enum labels cannot be added and used in the same transaction.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE notification_kind "
            "ADD VALUE IF NOT EXISTS 'malicious_detected'"
        )


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only (CLAUDE.md §6).")
