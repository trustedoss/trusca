# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""allow one in-flight scan per branch instead of one per project

Revision ID: 0047
Revises: 0046
Create Date: 2026-08-04

Phase: branch as a first-class axis (follows 0046)
Kind: schema (replaces one partial unique index; no data migration)
Forward-only: yes

What:
  ``ix_scans_project_active`` is rebuilt on ``(project_id, ref)`` with
  ``NULLS NOT DISTINCT``, keeping its ``status IN ('queued','running')``
  predicate.

Why:
  The index enforced at most one in-flight scan per PROJECT. Once branches
  became a first-class axis — the current-state anchor prefers the main line,
  the reads filter by ref — that limit stopped matching the model: pushing to
  ``main`` and ``release/1.x`` at the same time made one CI job wait for the
  other's scan, or fail with a 409 it could do nothing about. The branches
  write to disjoint snapshots, so nothing about the data required them to be
  serialized.

Why NULLS NOT DISTINCT:
  ``ref`` is NULL for ad-hoc scans, which is the majority of rows. Under the
  default NULLS DISTINCT a plain ``(project_id, ref)`` unique index would stop
  constraining them at all — every manual re-trigger would queue another scan,
  turning a stability guard into nothing for the common case. NULLS NOT
  DISTINCT (PostgreSQL 15+; we pin 17) makes all ref-less rows of a project
  collide with each other, so ad-hoc scans keep exactly today's behaviour and
  only *named branches* gain concurrency.

Blast radius:
  A project can now hold as many in-flight scans as it has distinct refs. The
  bound that matters is the per-team cap (``_enforce_team_concurrency_cap``)
  and the per-user trigger rate limit, both unchanged; the disk guard still
  refuses new work when the workspace is full.

Locking / build strategy:
  DROP then CREATE inside the migration transaction. The guard is absent for
  that window, which is safe because ``scripts/upgrade.sh`` stops the app
  containers before migrating — nothing can trigger a scan meanwhile. Doing it
  the other way (create first, drop after) is impossible: the two indexes would
  both have to hold, and the old one is exactly what we are relaxing.

Migration policy (CLAUDE.md §6):
  - No column changes, no backfill, no data migration.
  - Forward-only: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_scans_project_active")
    # Mirrors the Index(...) declaration in models.scan.Scan.__table_args__ —
    # keep the two in step or ``alembic check`` reports the schema as drifted.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_scans_project_active
            ON scans (project_id, ref)
            NULLS NOT DISTINCT
         WHERE status IN ('queued','running')
        """
    )


def downgrade() -> None:
    raise NotImplementedError("forward-only migration (CLAUDE.md §6)")
