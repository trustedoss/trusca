"""scan_last_rematched_at — W6-#42 vulnerability rematch watermark

Revision ID: 0026
Revises: 0025
Created: 2026-05-27

Phase: post-v2.3 W6 (DT removal — Trivy single-engine replacement)
PR: W6-#42 (automatic re-matching beat)
Kind: schema
Forward-only: yes

What:
  - Add column ``scans.last_rematched_at`` (TIMESTAMPTZ, nullable).
  - Add partial index
        ix_scans_rematch_due (last_rematched_at NULLS FIRST)
        WHERE status = 'succeeded'
    so the rematch beat's "find succeeded scans due for re-matching" query is
    O(log N) on the work set instead of O(all scans). NULLS FIRST surfaces
    never-rematched scans before any time-bucketed cohort.

Why:
  - W6-#42 promotes a DT killer feature (automatic re-matching when the vuln
    database changes) into a Trivy-backed Celery beat. The beat enumerates
    ``succeeded`` scans whose preserved tarball carries the cdxgen SBOM and
    whose ``last_rematched_at`` is either NULL (never rematched) or older than
    the configured interval (default 6h), then fans out one
    ``rematch_scan_findings`` task per due scan.
  - Without a column we would have to fall back to "rematch every succeeded
    scan every cycle", which would (a) hammer the worker pool with redundant
    Trivy runs and (b) lose the natural per-scan throttling that lets the beat
    safely scale to thousands of scans without backlog.
  - A partial index is correct here: rematch only ever targets ``status =
    'succeeded'`` rows; queued / running / failed / cancelled rows are
    ineligible by definition. The partial keeps the index size proportional
    to the eligible cohort (~all production scans) rather than the historical
    cancelled-scan tail.

Backfill:
  - None. Existing succeeded scans get ``last_rematched_at = NULL`` and the
    beat treats NULL as "never rematched → due now" — the first beat cycle
    after the migration naturally schedules them in batches honouring
    ``VULN_REMATCH_BATCH_SIZE`` (defined in code). This is intentional: a
    user upgrading to v2.4.0 gets the new feature applied to their existing
    SBOM corpus on the next 6h tick.

Downgrade:
  - Forward-only per CLAUDE.md §6 (post-GA migration policy). ``downgrade()``
    is a no-op for the Alembic test harness; do not run it in production.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column(
            "last_rematched_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_scans_rematch_due",
        "scans",
        ["last_rematched_at"],
        unique=False,
        postgresql_where=sa.text("status = 'succeeded'"),
    )


def downgrade() -> None:
    """Forward-only per CLAUDE.md §6 — do not run in production."""
