"""three indexes the hot read paths were missing

Revision ID: 0078
Revises: 0077
Create Date: 2026-09-04

Kind: schema (additive indexes; no column change, no data migration)
Forward-only: yes

What:
  - ``ix_audit_logs_target_table_target_id_created_at``
    on ``audit_logs (target_table, target_id, created_at)``
  - ``ix_audit_logs_actor_created_at``
    on ``audit_logs (actor_user_id, created_at)``
  - ``ix_projects_team_updated_active``
    on ``projects (team_id, updated_at DESC) WHERE archived_at IS NULL``

Why, with the plans that motivated each

1. The finding history panel. Opening a vulnerability runs two queries in
   ``services/vulnerability_service`` that filter
   ``target_table = 'vulnerability_findings' AND target_id = <finding>`` and
   order by ``created_at``. ``ix_audit_logs_target_table`` matches the first
   predicate and nothing else, and ``vulnerability_findings`` is the largest
   bucket that column has: the scan pipeline writes one create row per
   finding, so every scan adds thousands. Measured on 200,000 rows, reading
   five: bitmap scan over 3,309 buffers, 28,566 rows discarded by the filter,
   116 ms. With the index: 52 buffers, 2.7 ms.

2. The admin audit search filtered by actor. ``ix_audit_logs_actor_user_id``
   selects the actor's rows and leaves the ordering to a sort, and with a
   ``LIMIT`` the planner prefers walking ``ix_audit_logs_created_at`` backwards
   and discarding everything by another actor. Measured on 400,000 rows,
   fetching 50: 4,734 buffers and 4,748 rows discarded, 12 ms. With the index:
   44 buffers, 0.9 ms, no sort.

3. The project list, which is also what the dashboard reads. The query is
   ``WHERE team_id = ? AND archived_at IS NULL ORDER BY updated_at DESC``.
   ``ix_projects_team_archived`` covers the two predicates and its own comment
   names this query, but it carries no ``updated_at``, so the ordering is a
   sort over every matching row. Partial on ``archived_at IS NULL`` because
   that is the only half the list reads and archived projects should not pay
   for its upkeep. Measured on 60,000 projects across 20 teams, fetching 100:
   802 buffers and a top-N sort over 3,000 rows. With the index: 37 buffers.

Notes:
  - Additive only. Nothing reads differently, so no behaviour changes and
    nothing needs a backfill.
  - NOT created ``CONCURRENTLY``, matching every index revision in this tree
    (0007, 0015, 0018, 0023, ...): Alembic runs each migration in a
    transaction and ``CREATE INDEX CONCURRENTLY`` cannot run inside one. The
    build takes a write lock on the table for its duration. On a small
    deployment that is imperceptible; on one with a large ``audit_logs``, plan
    a maintenance window or pre-build the three indexes online out of band,
    after which this migration finds them present. The exact statements are in
    the upgrade notes.
  - No new tables, so no GRANT: index creation needs none, and the tables
    themselves already carry the app role's privileges.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0078"
down_revision: str | None = "0077"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_target_table_target_id_created_at",
        "audit_logs",
        ["target_table", "target_id", "created_at"],
    )
    op.create_index(
        "ix_audit_logs_actor_created_at",
        "audit_logs",
        ["actor_user_id", "created_at"],
    )
    # DESC in the index matches the ORDER BY so the scan runs forward rather
    # than backward. Postgres can walk either direction, so this is about
    # matching the query's shape rather than about capability.
    op.create_index(
        "ix_projects_team_updated_active",
        "projects",
        ["team_id", sa.text("updated_at DESC")],
        postgresql_where=sa.text("archived_at IS NULL"),
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
