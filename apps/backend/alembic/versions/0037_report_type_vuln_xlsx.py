"""report_type += vuln_xlsx

Revision ID: 0037
Revises: 0036
Created: 2026-07-03

Phase: G (Excel vulnerability report)
Kind: schema
Forward-only: yes

What:
  - Add ``vuln_xlsx`` to the ``report_type_enum`` Postgres enum.

Why:
  - The new ``GET /projects/{id}/vulnerability-report.xlsx`` endpoint records a
    Reports-center history row via ``record_report_download(report_type=
    "vuln_xlsx")``. ``report_downloads.report_type`` is a native Postgres enum
    (created in mig 0025, ``report_type_enum``), so that INSERT is rejected at
    the DB layer until the type accepts the value. This revision adds it up
    front so the endpoint ships purely as application code. The model tuple
    ``REPORT_TYPE_VALUES`` (models/report_download.py) is updated in the same PR.

Backfill:
  - None. Additive enum value; existing rows unaffected.

Downgrade:
  - Forward-only per CLAUDE.md §6. Postgres cannot drop an enum value without a
    full type rebuild (and that would orphan any row already tagged
    ``vuln_xlsx``); we never remove emitted report types.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG 12+ permits ALTER TYPE ... ADD VALUE inside a transaction (the new
    # value just cannot be USED in the same transaction — we only add it here).
    # IF NOT EXISTS keeps the migration idempotent across partial re-runs.
    op.execute("ALTER TYPE report_type_enum ADD VALUE IF NOT EXISTS 'vuln_xlsx'")


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6). Removing an enum value requires a type
    # rebuild and risks orphaning rows; we never drop emitted report types.
    pass
