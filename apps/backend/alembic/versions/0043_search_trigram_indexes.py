# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""search foundations — pg_trgm + GIN trigram indexes (S1-1)

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-30

Phase: S1 (search foundations)
Kind: schema (additive — one extension + five indexes; no data migration)
Forward-only: yes

What:
  - ``CREATE EXTENSION pg_trgm`` (contrib, shipped with the postgres:17 image
    we pin, so this adds no new deployment dependency and works air-gapped).
  - Five GIN trigram indexes on the columns every search endpoint matches with
    a leading-wildcard ``ILIKE '%term%'``:
      * ``components.name``            — component search (project tab, global)
      * ``components.purl``            — global search, and the project tab
                                         after S1-4 switches its second axis
      * ``vulnerabilities.external_id``— CVE lookup everywhere
      * ``vulnerabilities.summary``    — CVE text search (project tab)
      * ``projects.name``              — project list + global search

Why:
  Every one of the portal's 13 search surfaces matches with a leading wildcard.
  A b-tree cannot serve ``'%foo%'`` — Postgres falls back to a sequential scan
  on every keystroke of a debounced search box. ``gin_trgm_ops`` is the index
  type that *does* serve leading wildcards, and it is also what makes
  ``similarity()`` ranking affordable in S3. Building it here means S2/S3/S4
  inherit a searchable schema instead of each re-litigating index strategy.

Which columns were deliberately left out:
  - ``licenses.name`` / ``licenses.spdx_id`` — the catalog is 52 rows; a GIN
    index costs more to maintain than the scan it would save.
  - ``users.email`` / ``teams.name`` — admin-only surfaces over small tables.
  - ``components.namespace`` — S1-4 removes it from the search path; the
    ingest never populates it (see ``tasks/scan_source.py`` component upsert).
  - ``audit_logs.diff`` — a cast-to-text ILIKE over a JSONB column; indexing it
    would mean an expression index on the cast, and the admin audit surface is
    already date-bounded.

Locking / build strategy:
  Plain (non-CONCURRENT) ``CREATE INDEX``, which takes an ACCESS EXCLUSIVE lock
  on each table for the duration of the build. This is the right trade here:
  the product upgrades through ``scripts/upgrade.sh``, which stops the app
  containers before running migrations, so there is no live traffic to block —
  and CONCURRENTLY cannot run inside Alembic's migration transaction, so using
  it would mean an AUTOCOMMIT escape hatch that can leave an INVALID index
  behind if the build fails midway. A failed plain build simply rolls back.

Migration policy (CLAUDE.md §6):
  - Additive only; no column changes, no backfill, no data migration.
  - Forward-only: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (index name, table, column) — the same five indexes are declared as
# ``Index(...)`` entries on the models in ``models/scan.py``. Two copies of one
# vocabulary, so a contract test asserts set equality between them:
# ``tests/unit/test_search_index_contracts.py`` (hardening rule 2).
_TRIGRAM_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_components_name_trgm", "components", "name"),
    ("ix_components_purl_trgm", "components", "purl"),
    ("ix_vulnerabilities_external_id_trgm", "vulnerabilities", "external_id"),
    ("ix_vulnerabilities_summary_trgm", "vulnerabilities", "summary"),
    ("ix_projects_name_trgm", "projects", "name"),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for index_name, table, column in _TRIGRAM_INDEXES:
        op.create_index(
            index_name,
            table,
            [column],
            unique=False,
            postgresql_using="gin",
            postgresql_ops={column: "gin_trgm_ops"},
        )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
