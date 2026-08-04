# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""index the version label so a project's releases can be looked up by name

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-04

Phase: release vocabulary (label-keyed supersede)
Kind: schema (additive — one expression index; no data migration)
Forward-only: yes

What:
  ``ix_scans_project_release_label`` — a b-tree over
  ``(project_id, btrim(metadata ->> 'release'))``, partial on rows that carry a
  non-blank string label.

Why:
  ``tasks.scan_retention.supersede_prior_release_scans`` runs on every
  succeeded scan that names a version and asks "which earlier scans of this
  project claim this same label?". Without an index that is a sequential scan
  over the project's whole scan history inside the finalize transaction — the
  one place a slow query stalls a user-visible pipeline. The same index serves
  the read direction ("which snapshot is 4.0?"), which is the point of having
  labels at all.

  The predicate mirrors ``_release_absent`` exactly (JSON type must be
  ``string``, trimmed value non-blank) so the index covers precisely the rows
  the query filters to and nothing else. Trimming inside the index matters:
  the supersede compares trimmed labels, so " 4.0" and "4.0" have to land on
  the same key or the index would not be usable for the lookup.

Why not a UNIQUE index:
  Uniqueness would have to be enforced against a moving target — a label is
  claimed at scan-create time but only becomes live when the scan succeeds, so
  the constraint would need to span (label, status, superseded_at) and could
  abort a scan at finalize, after all the work is done. The supersede rule
  already yields "one live snapshot per label" without a failure mode that
  destroys a completed scan's results, so the invariant is maintained in the
  finalize transaction rather than by the DB refusing a write.

Locking / build strategy:
  Plain (non-CONCURRENT) ``CREATE INDEX``. ``scripts/upgrade.sh`` stops the app
  containers before migrating, so there is no live traffic to block, and
  CONCURRENTLY cannot run inside Alembic's transaction (a failed build would
  leave an INVALID index behind). A failed plain build simply rolls back.

Migration policy (CLAUDE.md §6):
  - Additive only; no column changes, no backfill, no data migration.
  - Forward-only: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    # Spelled as a literal rather than an interpolated constant: every other
    # migration in this tree passes literal DDL to op.execute(), and semgrep's
    # sqlalchemy-execute-raw-query rule flags the variable form on sight. There
    # is nothing to interpolate here anyway.
    #
    # Mirrors the Index(...) declaration in models.scan.Scan.__table_args__ —
    # keep the two in step or ``alembic check`` reports the schema as drifted.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_scans_project_release_label
            ON scans (project_id, btrim(metadata ->> 'release'))
         WHERE jsonb_typeof(metadata -> 'release') = 'string'
           AND btrim(metadata ->> 'release') <> ''
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CLAUDE.md §6)")
