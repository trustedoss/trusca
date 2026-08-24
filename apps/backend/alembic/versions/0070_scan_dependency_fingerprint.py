# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""scan dependency fingerprint

Revision ID: 0070
Revises: 0069
Create Date: 2026-08-24

Phase: concurrency-scaling-plan-2026-08-22.md §3.2 S8 (unit 29), schema step
PR: (opened together with the follow-on reuse-decision revision)
Kind: schema (additive, one nullable column; no data migration)
Forward-only: yes

What:
  ``scans.dependency_fingerprint``: a SHA-256 hex digest (64 chars, fixed
  length) over the scan's manifest/lockfile hashes, the cdxgen scanner
  version, and the scan-time config that shapes the generated SBOM. Nullable
  ``VARCHAR(64)``, no default.

Why:
  S8 skips re-running cdxgen when a commit's dependency set has not changed
  since the project's last successful scan, reusing that scan's preserved
  SBOM for vulnerability re-matching instead (the reuse machinery already
  exists: ``tasks.vulnerability_rematch.preserved_tarball_has_sbom`` +
  ``rematch_scan_findings``). Deciding "has not changed" needs something to
  compare against, and nothing on the ``scans`` row currently records what a
  scan's dependency set actually was. This migration adds the one column the
  comparison needs; the fingerprint is computed and written by
  ``models.scan_fingerprint.compute_scan_fingerprint`` at scan-success time
  (wired into the pipeline in a follow-on change, see the plan's §7.1 unit
  29 "머지 단위: 연속").

  This is a schema-only step by design (plan §8: "스키마를 건드리는 단위 ...
  forward-only 마이그레이션이고 다운그레이드를 두지 않는다"). The column is
  added and populated going forward; no backfill is attempted for scans that
  already succeeded, because their preserved source tree is gone by the time
  this migration runs for most of them (retained latest-succeeded-per-project
  only, see migration 0051's rationale) and a fingerprint computed from
  nothing would be indistinguishable from one computed from a since-deleted
  tree that no longer matches. NULL is deliberately not a value the reuse
  decision (a later revision) will treat as a match.

Why a column and not a table:
  One scalar per scan, read for exactly one comparison ("does this scan's
  fingerprint match the prior succeeded scan's for the same (project_id,
  ref)?"), never filtered or aggregated across scans. A satellite table would
  need its own FK, index, and cascade-delete wiring to answer a question a
  single column already answers.

Index:
  None. The lookup this column supports is "the latest succeeded scan for
  this (project_id, ref)", already served by the existing partial index
  ``ix_scans_project_ref`` (status = 'succeeded'). The reuse decision reads
  that row's ``dependency_fingerprint`` scalar directly; it does not search
  BY fingerprint, so no new index earns its write cost.

Reversal:
  Forward-only, and additive. With the column unread by anything outside the
  (not-yet-wired) reuse decision, every existing code path is unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "scans",
        sa.Column("dependency_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    # Forward-only (CLAUDE.md §6).
    raise NotImplementedError("0070 is forward-only")
