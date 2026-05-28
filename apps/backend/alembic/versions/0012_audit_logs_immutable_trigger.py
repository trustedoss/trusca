"""audit_logs immutability — BEFORE UPDATE OR DELETE trigger

Revision ID: 0012
Revises: 0011
Created: 2026-05-10

Phase: post-walkthrough stabilization (A bundle)
PR: fix/walkthrough-system-bugs
Kind: schema (DDL-only, no data migration)
Forward-only: yes

What:
  - Create the function ``audit_logs_prevent_mutation()`` that raises
    ``SQLSTATE 23000`` (integrity_constraint_violation) carrying a human
    message that names the offending TG_OP.
  - Attach the function as **two** triggers on ``audit_logs``:
      1. ``audit_logs_immutable_trigger`` — BEFORE UPDATE OR DELETE,
         FOR EACH ROW (catches single / batched DML).
      2. ``audit_logs_immutable_truncate`` — BEFORE TRUNCATE,
         FOR EACH STATEMENT (PostgreSQL fires row triggers only on
         UPDATE/DELETE; TRUNCATE bypasses them, which would otherwise
         let a privileged session wipe the audit trail in one statement
         — the very threat model the trigger exists to defeat).
    INSERT is unaffected — the listener path that emits audit rows
    continues to work without code changes.

Why (sys-bug-audit-1, walkthrough 2026-05-09 / -10):
  - The append-only contract for ``audit_logs`` was previously enforced
    only at the application layer (the listener never emits UPDATE or
    DELETE; the admin API exposes no mutating endpoints). A super-admin
    with raw psql access could, in theory, edit or delete rows without
    the application noticing — defeating the audit log's compliance
    promise. The trigger closes that gap by making mutation a
    DB-level error regardless of session source.
  - PR #44 documented "DB-level immutability" as roadmap. This migration
    ships the roadmap item; the audit-log admin manual is updated in the
    same PR to flip the doc back to "enforced at the DB".

Adversarial reasoning (memory ``feedback_adversarial_input_parametrize``):
  - BEFORE-row trigger fires on any UPDATE that mutates a content column —
    there is no "set only one column" loophole on the immutable surface.
  - BEFORE TRUNCATE statement-level trigger covers the single-statement
    table-wipe bypass that BEFORE-row cannot intercept.
  - The trigger does NOT block INSERT — the audit listener path stays
    functional. Concurrent INSERTs on the same table are not affected
    (BEFORE UPDATE OR DELETE is a no-op for INSERT).
  - **FK cascade SET NULL is allowed** on ``actor_user_id`` and
    ``team_id``. Both columns reference parents (``users``, ``teams``)
    with ``ON DELETE SET NULL`` so a User / Team deletion would
    otherwise fire an UPDATE to NULL the FK column on every prior audit
    row — that propagation must succeed or every legitimate User / Team
    delete with prior audit history would 500. The function gates on
    ``NEW.<col> IS NOT NULL`` so the legitimate cascade (non-NULL → NULL)
    flows through, while a tampering rotation between two non-NULL ids
    ("it wasn't me, it was the other admin") is refused.
  - **Known residual bypass — role privilege**: in the default install
    (docker-compose.dev.yml + docker-compose.yml), Alembic migrations
    AND the FastAPI / Celery runtime share a single PostgreSQL role
    (``trustedoss``). That role owns the function + triggers, which
    means it can ``DROP TRIGGER`` / ``ALTER FUNCTION ... OWNER`` and
    bypass the gate via "DROP TRIGGER → UPDATE → re-CREATE TRIGGER".
    This downgrades the trigger to "structural enforcement against
    honest mistakes and most external SQL-injection paths" rather than
    "absolute". A Phase 7/8 hardening PR is expected to split the
    runtime role from the migration role (``trustedoss_app`` DML-only
    on ``audit_logs`` + ``trustedoss_owner`` for migrations) at which
    point the trigger becomes unbypassable from the runtime app. The
    follow-up is tracked on the security-reviewer report for this PR.
  - For now, the bypass remains observable: ``DROP TRIGGER`` is a DDL
    statement, captured by ``pg_event_trigger`` (future audit-of-audit
    hardening) and by the operator's own session log for two-operator
    retention purges.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    ``NotImplementedError``. Manual rollback is ``DROP TRIGGER
    audit_logs_immutable_truncate ON audit_logs; DROP TRIGGER
    audit_logs_immutable_trigger ON audit_logs; DROP FUNCTION
    audit_logs_prevent_mutation();`` if a critical incident demands it.
    Drop the TRUNCATE trigger first so the row trigger continues to
    protect the table for the brief window before the row trigger drop.
  - SQLSTATE 23000 (integrity_constraint_violation) classifies cleanly
    as ``IntegrityError`` in SQLAlchemy / asyncpg, so the application
    layer can catch it identically to NOT NULL / FK violations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION_DDL = """
CREATE OR REPLACE FUNCTION audit_logs_prevent_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=%)', TG_OP
      USING ERRCODE = '23000';
  END IF;

  -- TG_OP = 'UPDATE' from here on.
  -- Strict: every content column is immutable. These nine carry the
  -- evidentiary record (who did what, when, against what, with which
  -- diff). A change to any one is tampering.
  IF (OLD.id, OLD.created_at, OLD.action, OLD.target_table, OLD.target_id,
      OLD.request_id, OLD.ip, OLD.user_agent, OLD.diff)
     IS DISTINCT FROM
     (NEW.id, NEW.created_at, NEW.action, NEW.target_table, NEW.target_id,
      NEW.request_id, NEW.ip, NEW.user_agent, NEW.diff)
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on content column)'
      USING ERRCODE = '23000';
  END IF;

  -- actor_user_id and team_id are FK columns with ON DELETE SET NULL on
  -- their parent tables. When a User or Team row is removed, Postgres
  -- propagates the cascade by UPDATEing referencing audit_logs rows to
  -- NULL their FK column. Allow that exact transition (any → NULL) but
  -- refuse any other change — rotating to a different non-NULL id would
  -- be a framing attack.
  IF NEW.actor_user_id IS NOT NULL
     AND OLD.actor_user_id IS DISTINCT FROM NEW.actor_user_id
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on actor_user_id pin)'
      USING ERRCODE = '23000';
  END IF;
  IF NEW.team_id IS NOT NULL
     AND OLD.team_id IS DISTINCT FROM NEW.team_id
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on team_id pin)'
      USING ERRCODE = '23000';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""".strip()


_TRIGGER_DDL = """
CREATE TRIGGER audit_logs_immutable_trigger
BEFORE UPDATE OR DELETE ON audit_logs
FOR EACH ROW EXECUTE FUNCTION audit_logs_prevent_mutation();
""".strip()


# BEFORE TRUNCATE must be FOR EACH STATEMENT (TRUNCATE has no per-row
# events to attach to). Without this trigger a single ``TRUNCATE TABLE
# audit_logs;`` would silently wipe the audit trail despite the row
# trigger above.
_TRUNCATE_TRIGGER_DDL = """
CREATE TRIGGER audit_logs_immutable_truncate
BEFORE TRUNCATE ON audit_logs
FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_prevent_mutation();
""".strip()


def upgrade() -> None:
    op.execute(_FUNCTION_DDL)
    op.execute(_TRIGGER_DDL)
    op.execute(_TRUNCATE_TRIGGER_DDL)


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
