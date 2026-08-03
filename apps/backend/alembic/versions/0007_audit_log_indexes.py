"""audit_logs target_table / action / composite indexes — admin Audit Log search

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-07

Phase: 4 (PR #14 — Admin Audit Log search + CSV export)
PR: #14
Kind: schema (index-only, additive)
Forward-only: yes

What:
  - Add three indexes to the existing ``audit_logs`` table::
      ix_audit_logs_target_table              (target_table)
      ix_audit_logs_action                    (action)
      ix_audit_logs_target_actor_created      (target_table, actor_user_id, created_at)

Why:
  - The Phase 4 admin Audit Log search endpoint
    (``GET /v1/admin/audit``) filters by ``target_table`` (whitelist enum
    of 10+ table names) and / or ``action`` (e.g. ``create`` / ``update`` /
    ``delete``). Without these indexes a search across millions of rows
    would degrade to a full sequential scan.
  - The compound ``(target_table, actor_user_id, created_at)`` matches the
    most common Admin UI query: "show every audit row for table X by
    user Y, newest first" — Postgres can use this single index to satisfy
    the WHERE + ORDER BY without a sort step.
  - The CSV export endpoint streams the same filtered set, so the admin
    can dump months of history without timing out.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``. Dropping an index is a manual / scripted op
    if needed — but indexes are additive and safe to leave in place.
  - These are pure CREATE INDEX statements, no DDL on the table itself,
    so no data-migration concerns. Postgres holds a SHARE lock on the
    table during build but that does not block readers (and the audit
    table is append-only — no concurrent UPDATE / DELETE traffic to
    contend with).
  - No CONCURRENTLY: this codebase is small (audit_logs is bounded by
    request volume × retention) and the dev / CI databases have no live
    traffic. Production deployments with a multi-million-row audit table
    can switch to ``CREATE INDEX CONCURRENTLY`` via a manual op-script
    follow-up; for now, atomic + fast is the right trade-off.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_logs_target_table",
        "audit_logs",
        ["target_table"],
    )
    op.create_index(
        "ix_audit_logs_action",
        "audit_logs",
        ["action"],
    )
    op.create_index(
        "ix_audit_logs_target_actor_created",
        "audit_logs",
        ["target_table", "actor_user_id", "created_at"],
    )


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
