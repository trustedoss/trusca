"""scan_retention — DT-style ref-keyed retention (superseded columns + indexes)

Revision ID: 0028
Revises: 0027
Created: 2026-05-30

Phase: post-v2.4 (scan-retention plan)
Kind: schema
Forward-only: yes

What:
  - Add three columns to ``scans``::
        ref                    VARCHAR(255)            -- normalized git ref
        superseded_at          TIMESTAMPTZ             -- stamped when retired
        superseded_by_scan_id  UUID REFERENCES scans(id) ON DELETE SET NULL
  - Recreate ``ix_scans_rematch_due`` so its partial predicate also excludes
    superseded scans (``status = 'succeeded' AND superseded_at IS NULL``).
  - Add ``ix_scans_project_ref`` — partial (project_id, ref) on
    status='succeeded' for the ref-keyed retire lookup.
  - Add ``ix_scans_superseded`` — partial (superseded_at) on
    superseded_at IS NOT NULL for the retention beat's reclaim sweep.

Why:
  - CI/webhook triggers a scan on every PR merge / push. Today every succeeded
    scan persists forever as an immutable release snapshot — ``scans`` +
    ``scan_components`` / ``vulnerability_findings`` / ``license_findings`` grow
    monotonically with no retention or delete path. This is the DB-side of the
    disk-artifact retention the beat cleaners already handle.
  - DT-style model (chosen over Snyk's stateless gate to keep "results live in
    the UI"): scans are kept but the same (project, ref) target keeps only its
    latest succeeded snapshot — older ones are stamped ``superseded_at`` and
    reclaimed after a grace period. Scans carrying an explicit
    ``metadata.release`` label are never superseded.
  - ``ref`` is a real column (not a JSONB expression index) so the retire query
    is index-driven and so webhook (``refs/heads/main``) and CI (``$GITHUB_REF``)
    triggers converge on one normalized value via
    ``services.scan_service.normalize_ref``.

Backfill:
  - None. Existing rows get ``ref = NULL`` / ``superseded_at = NULL`` and are
    treated as "no ref" by retention (covered by KEEP_LAST / MAX_AGE, never by
    ref-keyed retire). The first beat run reclaims aged excess per its policy.

Downgrade:
  - Forward-only per CLAUDE.md §6. ``downgrade()`` raises NotImplementedError.

Notes:
  - The self-referential FK (``superseded_by_scan_id`` → ``scans.id``) uses
    ON DELETE SET NULL so reclaiming a winner never blocks on or cascades to
    rows that merely point at it.
  - Index DDL uses ``postgresql_where`` (partial indexes) — no raw SQL, so no
    asyncpg ``::`` bind concerns.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID_PK = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Columns
    # ------------------------------------------------------------------
    op.add_column("scans", sa.Column("ref", sa.String(length=255), nullable=True))
    op.add_column(
        "scans",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scans",
        sa.Column("superseded_by_scan_id", UUID_PK, nullable=True),
    )
    op.create_foreign_key(
        "fk_scans_superseded_by_scan_id",
        "scans",
        "scans",
        ["superseded_by_scan_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # 2. Recreate the rematch-due partial index to exclude superseded scans
    # ------------------------------------------------------------------
    # A superseded snapshot has a newer winner for the same ref — re-matching it
    # would waste work and could fire stale notifications. Narrowing the partial
    # predicate keeps the index proportional to the live-succeeded cohort.
    op.drop_index("ix_scans_rematch_due", table_name="scans")
    op.create_index(
        "ix_scans_rematch_due",
        "scans",
        ["last_rematched_at"],
        postgresql_where=sa.text("status = 'succeeded' AND superseded_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # 3. Retention hot-path indexes
    # ------------------------------------------------------------------
    # ref-keyed retire: "the prior succeeded scan(s) for this (project, ref)".
    op.create_index(
        "ix_scans_project_ref",
        "scans",
        ["project_id", "ref"],
        postgresql_where=sa.text("status = 'succeeded'"),
    )
    # retention beat's "superseded past grace" sweep.
    op.create_index(
        "ix_scans_superseded",
        "scans",
        ["superseded_at"],
        postgresql_where=sa.text("superseded_at IS NOT NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
