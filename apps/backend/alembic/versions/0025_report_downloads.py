"""report_downloads — Reports-center download/export history (W3 #32a-1)

Revision ID: 0025
Revises: 0024
Created: 2026-05-26

Phase: post-v2.3 W3 (Reports center)
PR: #32a-1 (schema half — model + migration only)
Kind: schema
Forward-only: yes

What:
  - Create Postgres ENUM type ``report_type_enum`` with values:
        ('notice', 'sbom', 'vuln_pdf', 'vex_export')
  - Create table ``report_downloads``::
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid()
        project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE
        scan_id     UUID          REFERENCES scans(id)    ON DELETE SET NULL
        team_id     UUID NOT NULL REFERENCES teams(id)    ON DELETE CASCADE
        user_id     UUID          REFERENCES users(id)    ON DELETE SET NULL
        report_type report_type_enum NOT NULL
        format      VARCHAR(40) NOT NULL
        size_bytes  BIGINT
        client_ip   INET
        user_agent  VARCHAR(512)
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
  - Indexes (every FK + the two hot-path compound orderings):
        ix_report_downloads_project_created_at  (project_id, created_at DESC)
        ix_report_downloads_team_created_at     (team_id,    created_at DESC)
        ix_report_downloads_scan_id             (scan_id)
    The two compound (tenant, created_at DESC) indexes also cover the
    leftmost-prefix equality paths on project_id / team_id, so no separate
    single-column indexes are emitted for those FKs.

Why:
  - Reports center (#32a) needs persistent download / export history so the
    project Reports tab can render "what reports have been generated for this
    project, by whom, when, and in which format". The four covered emit
    types are:
      * notice      — NOTICE / attribution document
      * sbom        — CycloneDX or SPDX SBOM export
      * vuln_pdf    — vulnerability PDF report
      * vex_export  — CycloneDX VEX export (NOT a scan-bound artefact, so
                      ``scan_id`` is NULL for these rows by design — hence
                      ``scan_id`` is nullable + ON DELETE SET NULL)
  - Why a dedicated table (vs. piggy-backing on ``audit_logs``):
    ``audit_logs`` is driven by the ``before_flush`` SQLAlchemy listener,
    which only fires on INSERT/UPDATE/DELETE. Read-only download endpoints
    do no flush — they would be silently absent from the audit log. VEX
    *import* is a mutation and is naturally captured by the audit listener;
    the four *download* paths covered here need an explicit emit table.
  - ``team_id`` is denormalised on every row (mirrored from the parent
    project at insert time) so admin / team-wide queries can filter by
    tenant without joining ``projects`` — CLAUDE.md §1.2 "Tenancy:
    compound indexes lead with the tenant column."

Forward-compatibility:
  - ``format`` is a free VARCHAR(40), NOT an ENUM. New export formats appear
    on the timescale of feature work; an ``ALTER TYPE ADD VALUE`` migration
    per new format is more friction than value. The writers are the single
    point that sets the value.
  - ``report_type`` IS an ENUM because the four-type set is closed at the
    product-design level (#32a defines exactly these four emit paths).
    Future emit types are an ``ALTER TYPE ADD VALUE`` migration.

Append-only contract:
  - No ``updated_at``. A correction is a new row, not an UPDATE. No
    DB-level trigger is added in this revision — the append-only semantic
    is enforced by the application layer (the emit helper only INSERTs).
  - No DEFAULT on ``client_ip`` / ``user_agent`` / ``size_bytes`` — NULL is
    a legitimate "unknown at emit time" value for each.

Cascade policy:
  - ``project_id``  CASCADE  — orphan history without a project is noise.
  - ``team_id``     CASCADE  — mirrors ``projects.team_id`` cascade
                              (scan.py:163); same lifecycle.
  - ``scan_id``     SET NULL — preserve the history when a scan is pruned;
                              VEX exports are NULL-scan_id by design.
  - ``user_id``     SET NULL — preserve the history when a user is deleted;
                              "someone on the team got this file" is the
                              audit-relevant fact.

PII note:
  - ``client_ip`` (INET) and ``user_agent`` are operational PII. Masking +
    retention policy is owned by the backend-developer PR at emit time —
    this migration only declares the columns. ``ip`` in ``audit_logs``
    follows the same shape (90-day retention via a future purge task).

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``. Dropping the table and the ENUM type is a
    manual / scripted op if ever needed.
  - The ENUM type is created with a plain ``CREATE TYPE`` statement to
    mirror prior migrations (0003 / 0008 / 0010 / 0011); the model side
    binds with ``create_type=False`` so SQLAlchemy never emits a
    duplicate during metadata creation.
  - No JSONB column on this table → no GIN index required.
  - No raw SQL beyond the CREATE TYPE → no asyncpg ``::`` / TIMESTAMPTZ
    bind concerns.
  - No data migration — the table is new and empty.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UUID_PK = postgresql.UUID(as_uuid=True)
GEN_UUID = sa.text("gen_random_uuid()")
NOW = sa.text("now()")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Postgres ENUM type
    # ------------------------------------------------------------------
    # Plain CREATE TYPE — consistent with prior migrations (0003 / 0008 /
    # 0010 / 0011). The migration is forward-only and runs exactly once
    # per DB; no IF NOT EXISTS guard. The SQLAlchemy model binds with
    # create_type=False so the ORM never emits a duplicate.
    op.execute(
        "CREATE TYPE report_type_enum AS ENUM "
        "('notice', 'sbom', 'vuln_pdf', 'vex_export')"
    )

    report_type_col_type = postgresql.ENUM(
        "notice",
        "sbom",
        "vuln_pdf",
        "vex_export",
        name="report_type_enum",
        create_type=False,
    )

    # ------------------------------------------------------------------
    # 2. Main table
    # ------------------------------------------------------------------
    op.create_table(
        "report_downloads",
        sa.Column("id", UUID_PK, primary_key=True, server_default=GEN_UUID),
        # --- foreign keys ---
        sa.Column("project_id", UUID_PK, nullable=False),
        sa.Column("scan_id", UUID_PK, nullable=True),
        sa.Column("team_id", UUID_PK, nullable=False),
        sa.Column("user_id", UUID_PK, nullable=True),
        # --- payload ---
        sa.Column("report_type", report_type_col_type, nullable=False),
        sa.Column("format", sa.String(length=40), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("client_ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        # --- audit ---
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=NOW,
        ),
        # --- FK constraints ---
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_report_downloads_project_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scan_id"],
            ["scans.id"],
            name="fk_report_downloads_scan_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name="fk_report_downloads_team_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_report_downloads_user_id",
            ondelete="SET NULL",
        ),
    )

    # ------------------------------------------------------------------
    # 3. Hot-path indexes
    # ------------------------------------------------------------------
    # (project_id, created_at DESC) — project Reports tab "newest first".
    # Leftmost-prefix rule means this also covers plain project_id
    # equality lookups, so no standalone ix_report_downloads_project_id.
    op.create_index(
        "ix_report_downloads_project_created_at",
        "report_downloads",
        ["project_id", sa.text("created_at DESC")],
    )

    # (team_id, created_at DESC) — admin / team-wide history.
    # Covers plain team_id equality lookups too.
    op.create_index(
        "ix_report_downloads_team_created_at",
        "report_downloads",
        ["team_id", sa.text("created_at DESC")],
    )

    # FK index for "exports produced by this scan" lookups.
    op.create_index(
        "ix_report_downloads_scan_id",
        "report_downloads",
        ["scan_id"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
