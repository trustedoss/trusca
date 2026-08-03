"""scan_kind += sbom

Revision ID: 0032
Revises: 0031
Created: 2026-06-13

Phase: sbom-ingest (prereq schema)
Kind: schema
Forward-only: yes

What:
  - Add ``sbom`` to the ``scan_kind`` Postgres enum.

Why:
  - External CycloneDX SBOM ingest (a separate follow-up PR) creates Scan rows
    whose source is an uploaded SBOM rather than a source-tree or container
    scan. The ``scan_kind`` enum (created in mig 0003) is native Postgres, so a
    Scan INSERT with kind ``sbom`` is rejected at the DB layer until the type
    accepts it. This revision adds the value up front so the ingest PR ships
    purely as application code. The model tuple ``SCAN_KIND_VALUES`` and the
    wire ``ScanKind`` Literal are updated in the same PR; a parity test
    (tests/unit/test_catalog_contracts.py) keeps the three in lockstep.

Backfill:
  - None. Additive enum value; existing rows unaffected.

Downgrade:
  - Forward-only per CLAUDE.md §6. Postgres cannot drop an enum value without a
    full type rebuild (and that would orphan any row already tagged ``sbom``);
    we never remove emitted kinds.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # PG 12+ permits ALTER TYPE ... ADD VALUE inside a transaction (the new
    # value just cannot be USED in the same transaction — we only add it here).
    # IF NOT EXISTS keeps the migration idempotent across partial re-runs.
    op.execute("ALTER TYPE scan_kind ADD VALUE IF NOT EXISTS 'sbom'")


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6). Removing an enum value requires a type
    # rebuild and risks orphaning rows; we never drop emitted kinds.
    pass
