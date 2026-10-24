"""component_approvals.decided_license_ids (#566)

Revision ID: 0093
Revises: 0092
Create Date: 2026-10-24

Kind: schema (one new nullable column; no data migration)
Forward-only: yes

What:
  - Add ``component_approvals.decided_license_ids TEXT[] NULL``: the sorted,
    de-duplicated license identifiers the component carried in the project
    when the approval was decided (approved or rejected).

Why:
  - #566: after a re-scan, ``auto_create_pending_approvals`` opened a fresh
    Pending approval for a conditional-license component even when the
    project had already decided it, because its ON CONFLICT target is the
    partial unique index over open rows only. An approval is per component,
    not per version, so skipping every decided component would let a later
    version under a different license bypass review. Storing the license set
    the decision was made against lets the scan skip only when that set is
    unchanged.
  - NULL means "decided before the set was recorded". There is no backfill:
    the licenses a component carried at decision time cannot be recovered
    reliably from later scans, so those rows keep today's behaviour and are
    re-opened by the next scan that finds the component.
  - No GRANT: the existing table-level GRANT to ``trustedoss_app`` covers new
    columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "component_approvals",
        sa.Column("decided_license_ids", postgresql.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
