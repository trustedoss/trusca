"""projects: drop the plaintext webhook secret column (contract)

Revision ID: 0086
Revises: 0085
Create Date: 2026-09-05

Kind: schema (one column)
Forward-only: yes

The last of the three. 0084 added the ciphertext column, 0085 filled it from
the plaintext, and this removes the plaintext.

Running this leaves no way back to the old values: the column is gone and the
ciphertext is only readable with the key that wrote it. That is the point of
the change, and it is why the encryption key belongs in the upgrade notes
ahead of the upgrade rather than after it.

``webhook_secret`` stays in ``core.audit._SENSITIVE_COLUMNS`` after this. The
column is gone from ``projects``, but the mask is keyed on the column name and
an audit row written before this upgrade still carries that name. Removing the
entry would not unmask those rows (the diff is already masked), but it would
stop masking any future write under that name, and the name is not reserved.
"""

from __future__ import annotations

from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("projects", "webhook_secret")


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0086 is forward-only")
