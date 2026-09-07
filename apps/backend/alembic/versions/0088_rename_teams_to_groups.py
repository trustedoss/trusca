# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
"""rename teams to groups (PR 0-1 of the group-hierarchy rollout)

Revision ID: 0088
Revises: 0087
Create Date: 2026-09-07

Phase: 0 (group-hierarchy rollout, PR 0-1)
Kind: schema (pure rename — no new column, no data migration)
Forward-only: yes

What:
  - ``ALTER TABLE teams RENAME TO groups``, plus the table's own primary key,
    index and foreign-key constraint (``teams_pkey``, ``ix_teams_organization_id``,
    ``uq_teams_org_slug``, ``fk_teams_organization_id``) renamed to their
    ``groups``-prefixed equivalents. ``ALTER TABLE ... RENAME TO`` renames the
    table only; PostgreSQL does not rename its dependent index/constraint
    names, so each is renamed explicitly (measured on this database).
  - ``team_id`` -> ``group_id`` on the six tables this PR scopes: ``projects``,
    ``memberships``, ``audit_logs``, ``license_policies``, ``gate_policies``,
    ``api_keys``. Each table's ``team_id``-named index / foreign-key constraint
    is renamed alongside its column.
  - ``audit_logs_prevent_mutation()`` (the 0012/0080 append-only trigger
    function) is replaced so its two ``team_id`` references become
    ``group_id``. This is the one place a column rename does NOT propagate on
    its own: a PL/pgSQL function body is opaque text to
    ``ALTER TABLE ... RENAME COLUMN``, unlike a CHECK constraint or a partial
    index predicate (both verified below to auto-update). The replacement
    keeps 0080's exact logic and its pinned
    ``SET search_path = pg_catalog, public, pg_temp`` — see "If you are
    writing a database function" in 0080/0082's docstrings.

What this migration deliberately does NOT do (see the PR description for the
full rollout plan):
  - Rename ``team_id`` on the other seven tables that also carry a foreign key
    to ``teams``/``groups`` (``component_approvals``, ``component_intake_requests``,
    ``github_app_credentials``, ``notification_routing_rules``,
    ``obligation_fulfilments``, ``report_downloads``, ``transition_approvals``).
    Those columns keep the name ``team_id`` and keep working exactly as before
    — PostgreSQL updates a foreign key's target by object identity, not by
    name, so a column named ``team_id`` referencing ``groups.id`` after this
    migration is not a defect, it is simply out of this PR's scope. Follow-up
    PRs cover them together with their model files.
  - Add ``parent_group_id`` / ``path`` or any other hierarchy column — Phase 1.
  - Touch anything under ``services/``, ``api/``, ``core/`` or ``schemas/``.

Why:
  Group-hierarchy rollout PR 0-1: renaming ``teams`` to ``groups`` is the first
  of four PRs that together let a group nest under another group without
  limit. This PR changes only names (table, columns, the trigger body that
  spelled one of them out) — no new column, no behavioural change, no value
  ever written differs from what would have been written before. PRs 0-2
  through 0-4 migrate the ~596 call sites that read ``.team_id`` / import
  ``Team`` off of a SQLAlchemy ``synonym`` compatibility layer (see
  ``models/auth.py``, ``models/scan.py``, ``models/api_key.py``,
  ``models/license_policy.py``, ``models/gate_policy.py``), so this PR is
  mergeable on its own without a coordinated multi-file edit.

Verified on this database (PostgreSQL 17.2) before relying on it, because the
plan that motivated this PR assumed the opposite in two places:
  - A CHECK constraint's stored definition (e.g.
    ``ck_api_keys_scope_consistency``) is a parsed expression tree, not text;
    ``ALTER TABLE ... RENAME COLUMN`` updates it automatically. No migration
    action needed for any CHECK constraint here.
  - A partial index's predicate (e.g. ``uq_license_policies_org_default``'s
    ``WHERE team_id IS NULL``, ``uq_gate_policies_org_default``'s equivalent)
    is likewise a stored expression, not text, and updates automatically on
    column rename. Dropping and recreating either index is unnecessary; doing
    so would just cost an extra table scan for no behavioural difference. This
    contradicts the plan's assumption — see the PR description for the
    verification transcript.
  - What does NOT auto-update: PL/pgSQL function bodies. ``ALTER TABLE ...
    RENAME COLUMN`` does not touch ``pg_proc.prosrc`` for any function that
    merely happens to reference the old name in its text, which is exactly why
    ``audit_logs_prevent_mutation()`` needs the explicit ``CREATE OR REPLACE``
    below and the other five renames do not need an equivalent step.

Notes:
  - Renaming an unnamed (auto-generated) foreign key constraint follows the
    same ``<table>_<column>_fkey`` convention Postgres itself used to name it
    (``license_policies_team_id_fkey`` -> ``license_policies_group_id_fkey``,
    ``gate_policies_team_id_fkey`` -> ``gate_policies_group_id_fkey``) so the
    naming style in ``\\d`` output stays self-explanatory.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises ``NotImplementedError``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Same body as 0080's replacement of this function, with every ``team_id``
# reference (there were exactly two: the immutable-column subtraction and the
# FK-cascade pin) rewritten to ``group_id``. Nothing else changes: the ER32
# PII-scrub exception, the role check, and the search_path pin are copied
# verbatim from 0080.
_AUDIT_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_logs_prevent_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=%)', TG_OP
      USING ERRCODE = '23000';
  END IF;

  -- TG_OP = 'UPDATE' from here on.

  -- ER32 exception (0080). A user anonymisation must clear the subject's
  -- client details from their own audit rows, and both live inside the
  -- immutable set below. Unchanged by this migration: ip and user_agent are
  -- not the columns being renamed.
  IF current_setting('trusca.audit_scrub', true) = 'on'
     AND pg_has_role(current_user,
                     (SELECT relowner FROM pg_class
                       WHERE oid = 'public.audit_logs'::regclass), 'MEMBER')
     AND NEW.ip IS NULL AND NEW.user_agent IS NULL
     AND to_jsonb(OLD) - 'ip' - 'user_agent'
         IS NOT DISTINCT FROM
         to_jsonb(NEW) - 'ip' - 'user_agent'
  THEN
    RETURN NEW;
  END IF;

  -- Strict: every column is immutable except the two FK columns handled
  -- below, which Postgres itself rewrites on a parent delete. Stated by
  -- subtraction: a list of columns to protect is correct on the day it is
  -- written and unprotected the day somebody adds a column.
  IF to_jsonb(OLD) - 'actor_user_id' - 'group_id'
     IS DISTINCT FROM
     to_jsonb(NEW) - 'actor_user_id' - 'group_id'
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on content column)'
      USING ERRCODE = '23000',
            HINT = 'This guard covers every column, including ones added '
                   'after it was written, so a new column is immutable by '
                   'default. If a column genuinely needs to be updatable, '
                   'that is a decision to make in a migration that amends '
                   'this trigger, not a bug in the calling code.';
  END IF;

  -- actor_user_id and group_id (renamed from team_id by this migration) are
  -- FK columns with ON DELETE SET NULL on their parent tables. When a User
  -- or Group row is removed, Postgres propagates the cascade by UPDATEing
  -- referencing audit_logs rows to NULL their FK column. Allow that exact
  -- transition (any to NULL) but refuse any other change: rotating to a
  -- different non-NULL id would be a framing attack.
  IF NEW.actor_user_id IS NOT NULL
     AND OLD.actor_user_id IS DISTINCT FROM NEW.actor_user_id
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on actor_user_id pin)'
      USING ERRCODE = '23000';
  END IF;
  IF NEW.group_id IS NOT NULL
     AND OLD.group_id IS DISTINCT FROM NEW.group_id
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on group_id pin)'
      USING ERRCODE = '23000';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp;
""".strip()


def upgrade() -> None:
    # ------------------------------------------------------------------
    # teams -> groups
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE teams RENAME TO groups")
    op.execute("ALTER TABLE groups RENAME CONSTRAINT teams_pkey TO groups_pkey")
    op.execute(
        "ALTER TABLE groups RENAME CONSTRAINT fk_teams_organization_id "
        "TO fk_groups_organization_id"
    )
    op.execute(
        "ALTER TABLE groups RENAME CONSTRAINT uq_teams_org_slug TO uq_groups_org_slug"
    )
    op.execute(
        "ALTER INDEX ix_teams_organization_id RENAME TO ix_groups_organization_id"
    )

    # ------------------------------------------------------------------
    # projects.team_id -> group_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE projects RENAME COLUMN team_id TO group_id")
    op.execute(
        "ALTER TABLE projects RENAME CONSTRAINT fk_projects_team_id "
        "TO fk_projects_group_id"
    )
    op.execute(
        "ALTER TABLE projects RENAME CONSTRAINT uq_projects_team_slug "
        "TO uq_projects_group_slug"
    )
    op.execute("ALTER INDEX ix_projects_team_id RENAME TO ix_projects_group_id")
    op.execute(
        "ALTER INDEX ix_projects_team_archived RENAME TO ix_projects_group_archived"
    )
    op.execute(
        "ALTER INDEX ix_projects_team_updated_active "
        "RENAME TO ix_projects_group_updated_active"
    )

    # ------------------------------------------------------------------
    # memberships.team_id -> group_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE memberships RENAME COLUMN team_id TO group_id")
    op.execute(
        "ALTER TABLE memberships RENAME CONSTRAINT fk_memberships_team_id "
        "TO fk_memberships_group_id"
    )
    op.execute(
        "ALTER TABLE memberships RENAME CONSTRAINT uq_memberships_user_team "
        "TO uq_memberships_user_group"
    )
    op.execute("ALTER INDEX ix_memberships_team_id RENAME TO ix_memberships_group_id")
    op.execute(
        "ALTER INDEX ix_memberships_team_role RENAME TO ix_memberships_group_role"
    )

    # ------------------------------------------------------------------
    # audit_logs.team_id -> group_id (+ trigger function body)
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE audit_logs RENAME COLUMN team_id TO group_id")
    op.execute(
        "ALTER TABLE audit_logs RENAME CONSTRAINT fk_audit_logs_team_id "
        "TO fk_audit_logs_group_id"
    )
    op.execute("ALTER INDEX ix_audit_logs_team_id RENAME TO ix_audit_logs_group_id")
    op.execute(
        "ALTER INDEX ix_audit_logs_team_created_at "
        "RENAME TO ix_audit_logs_group_created_at"
    )
    op.execute(_AUDIT_TRIGGER_FUNCTION)

    # ------------------------------------------------------------------
    # license_policies.team_id -> group_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE license_policies RENAME COLUMN team_id TO group_id")
    op.execute(
        "ALTER TABLE license_policies RENAME CONSTRAINT "
        "license_policies_team_id_fkey TO license_policies_group_id_fkey"
    )
    op.execute(
        "ALTER TABLE license_policies RENAME CONSTRAINT "
        "uq_license_policies_org_team TO uq_license_policies_org_group"
    )
    op.execute(
        "ALTER INDEX ix_license_policies_team_id RENAME TO ix_license_policies_group_id"
    )
    # uq_license_policies_org_default: partial UNIQUE index, name carries no
    # "team" substring so no rename is needed. Its predicate
    # (``WHERE team_id IS NULL``) is a stored expression, not text, and
    # PostgreSQL rewrites it to ``group_id IS NULL`` as a side effect of the
    # RENAME COLUMN above — verified against this database before relying on
    # it (see module docstring).

    # ------------------------------------------------------------------
    # gate_policies.team_id -> group_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE gate_policies RENAME COLUMN team_id TO group_id")
    op.execute(
        "ALTER TABLE gate_policies RENAME CONSTRAINT "
        "gate_policies_team_id_fkey TO gate_policies_group_id_fkey"
    )
    op.execute(
        "ALTER TABLE gate_policies RENAME CONSTRAINT "
        "uq_gate_policies_org_team TO uq_gate_policies_org_group"
    )
    op.execute(
        "ALTER INDEX ix_gate_policies_team_id RENAME TO ix_gate_policies_group_id"
    )
    # uq_gate_policies_org_default: same reasoning as license_policies above.

    # ------------------------------------------------------------------
    # api_keys.team_id -> group_id
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE api_keys RENAME COLUMN team_id TO group_id")
    op.execute(
        "ALTER TABLE api_keys RENAME CONSTRAINT fk_api_keys_team_id "
        "TO fk_api_keys_group_id"
    )
    op.execute("ALTER INDEX ix_api_keys_team_id RENAME TO ix_api_keys_group_id")
    # ck_api_keys_scope_consistency references team_id in its body (not its
    # name) and is a stored expression like the partial indexes above — it
    # updates automatically to group_id. ck_api_keys_scope_values (the CHECK
    # on the *string* 'org'|'team'|'project') is untouched: this PR does not
    # rename the scope value 'team' itself.


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
