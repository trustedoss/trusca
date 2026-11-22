"""notifications.message_key + notifications.params

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-22

Kind: schema (two new nullable columns; no data migration)
Forward-only: yes

What:
  - Add ``notifications.message_key VARCHAR(64) NULL``: a stable identifier
    for the message a row carries (for example ``scan_scheduled_completed``).
  - Add ``notifications.params JSONB NULL``: the values that message names
    (project name, counts, identifiers).

Why:
  - ``title`` and ``body`` are English text composed by the producer, so a
    reader using the Korean UI saw English. With a key and its parameters the
    SPA renders the message from its own catalog in the reader's language.
  - ``kind`` alone is not enough: one kind carries several messages
    (``account_security`` has three, ``approval_pending`` is also used for an
    expired anonymisation request).
  - No backfill: rows written before this revision keep NULL in both columns
    and the SPA shows their stored ``title``/``body`` as before. Recovering a
    key from free text would be a guess.
  - No GRANT: the existing table-level GRANT to ``trustedoss_app`` covers new
    columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("message_key", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("params", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
