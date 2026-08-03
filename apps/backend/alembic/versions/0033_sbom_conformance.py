"""sbom_conformance — received-SBOM quality verdict (model 3)

Revision ID: 0033
Revises: 0032
Created: 2026-06-14

Phase: sbom-ingest (conformance)
Kind: schema
Forward-only: yes

What:
  - Create table ``sbom_conformance``::
        id                   UUID PK DEFAULT gen_random_uuid()
        scan_id              UUID NOT NULL UNIQUE REFERENCES scans(id)    ON DELETE CASCADE
        project_id           UUID NOT NULL        REFERENCES projects(id) ON DELETE CASCADE
        source_format        VARCHAR(16) NOT NULL   -- cyclonedx|spdx-json|spdx-tv|unknown
        result               VARCHAR(8)  NOT NULL   -- pass|warn|fail
        n_fail               INTEGER NOT NULL DEFAULT 0
        n_warn               INTEGER NOT NULL DEFAULT 0
        component_count      INTEGER NOT NULL DEFAULT 0
        purl_coverage_pct    INTEGER                -- NULL for SPDX Tag-Value
        license_coverage_pct INTEGER
        hash_coverage_pct    INTEGER
        checks               JSONB NOT NULL DEFAULT '[]'::jsonb
        created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
  - Index ``ix_sbom_conformance_project_id`` (project_id) for the
    "Received SBOM" tenant-scoped list. The UNIQUE on ``scan_id`` already
    backs the per-scan lookup (the conformance read endpoint) with an index.

Why:
  - An uploaded SBOM is scored for quality (services/sbom_conformance.py) before
    and regardless of CVE matching, so the portal can show a pass/warn/fail
    badge + per-check table and a supplier can be rejected with concrete
    reasons. ``result`` / coverage are queryable columns (list filtering /
    sorting); the full per-check array lives in ``checks`` (JSONB) for the
    detail view. A dedicated table (vs. scan_metadata JSONB) keeps those
    columns first-class and avoids the 16 KiB metadata ceiling.

Re-run idempotency:
  - ``scan_id`` is UNIQUE; the ingest Celery task deletes any prior row for the
    scan before inserting, so a Celery ``acks_late`` re-entry replaces rather
    than duplicates. No UPDATE path; a correction is a fresh row.

Cascade policy:
  - ``scan_id``    CASCADE — the verdict is meaningless without its scan.
  - ``project_id`` CASCADE — mirrors the scan→project lifecycle; a project
    delete cleans its verdicts.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
  - No native ENUM: ``result`` / ``source_format`` vocabularies are owned by the
    scorer (VARCHAR keeps tweaks migration-free; the FE mirrors the check-id
    catalogue under a contract test).
  - JSONB ``checks`` is read whole per row (detail view), never filtered by its
    interior → no GIN index needed.
  - No data migration — new empty table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_PK = postgresql.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")
EMPTY_JSONB_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "sbom_conformance",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        sa.Column("scan_id", UUID_PK, nullable=False),
        sa.Column("project_id", UUID_PK, nullable=False),
        sa.Column("source_format", sa.String(length=16), nullable=False),
        sa.Column("result", sa.String(length=8), nullable=False),
        sa.Column("n_fail", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("n_warn", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "component_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("purl_coverage_pct", sa.Integer(), nullable=True),
        sa.Column("license_coverage_pct", sa.Integer(), nullable=True),
        sa.Column("hash_coverage_pct", sa.Integer(), nullable=True),
        sa.Column(
            "checks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=EMPTY_JSONB_ARR,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_sbom_conformance_scan_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_sbom_conformance_project_id",
            ondelete="CASCADE",
        ),
        # One verdict per scan; also backs the per-scan read endpoint with an
        # index (UNIQUE implies an index).
        sa.UniqueConstraint("scan_id", name="uq_sbom_conformance_scan_id"),
    )

    # Tenant-scoped "Received SBOM" list ("conformance verdicts for my project").
    op.create_index(
        "ix_sbom_conformance_project_id",
        "sbom_conformance",
        ["project_id"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
