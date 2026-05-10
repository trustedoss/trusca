"""last super_admin protection — BEFORE UPDATE OR DELETE trigger on users

Revision ID: 0013
Revises: 0012
Created: 2026-05-10

Phase: post-walkthrough stabilization (A bundle, manual sys-bug fix A5)
PR: chore/manual-sys-bug-fix-a4-a5
Kind: schema (DDL-only, no data migration)
Forward-only: yes

What:
  - Create the function ``enforce_last_super_admin()`` that raises
    SQLSTATE 23514 (check_violation) when an UPDATE or DELETE would leave
    the system with zero active super_admins.
  - Attach it as ``trg_last_super_admin`` BEFORE UPDATE OR DELETE on
    ``users`` FOR EACH ROW.

Why (sys-bug-u&t-1, walkthrough 2026-05-09 / -10):
  - The application service (``services.admin_user_service``) already
    refuses to demote / deactivate the last active super_admin via the
    SELECT FOR UPDATE + ``LastSuperAdminProtected`` exception (PR #13,
    F-series finding from the walkthrough). That guard is correct under
    the FastAPI runtime — but a privileged operator with raw psql
    access (or any future code path that bypasses the service layer)
    can still ``UPDATE users SET is_superuser=false WHERE id=<last>``
    or ``DELETE FROM users WHERE id=<last>``, locking everyone out of
    the admin panel with no recovery short of a database surgery.
  - The trigger closes that gap by making "active super_admin = 0"
    a DB-level constraint the application cannot opt out of.

Active super_admin definition (matches admin_user_service):
  ``is_superuser = TRUE AND is_active = TRUE``

  ``is_active = FALSE`` is the system's soft-delete state (see
  ``deactivate_user`` in admin_user_service), so a deactivated super_admin
  does not count toward the protected-seat invariant. The schema does
  not have a separate ``deleted_at`` column (vs. the prompt's earlier
  draft) so the count predicate is simply the two booleans.

Adversarial reasoning (memory ``feedback_adversarial_input_parametrize``
+ ``feedback_security_reviewer_db_cascade_blind_spot``):
  - **Single-statement multi-row UPDATE / DELETE — SAFE**: BEFORE-row
    triggers fire per row, and plpgsql executes each per-row UPDATE
    with ``CommandCounterIncrement`` so the count subquery in row 2's
    trigger DOES see row 1's already-applied mutation (within the same
    statement). Concretely: ``UPDATE users SET is_superuser = FALSE
    WHERE is_superuser = TRUE AND is_active = TRUE`` against a
    2-active-super_admin baseline processes row 1 (count of others =
    1, allowed), then row 2's trigger fires and the count subquery
    returns 0 (row 1's mutation already visible) — RAISE. The trigger
    therefore covers the in-statement multi-row case end to end. The
    regression is pinned by ``test_trigger_blocks_multi_row_raw_demote``.
  - **Cross-transaction concurrent demote — NOT covered by the trigger**:
    two parallel transactions each holding a snapshot from before the
    other started see two active super_admins. Each demotes one. The
    trigger checks each row's count against its own snapshot; both
    pass; both commit; count drops to zero. The application-layer
    ``SELECT … FOR UPDATE`` in ``_lock_and_count_active_super_admins``
    serializes the two transactions so the second blocks until the
    first commits — that lock IS the defence here. The trigger only
    closes the bypass-the-application-layer surface (raw psql, future
    endpoints that forget the guard); it is NOT a substitute for the
    application lock. Removing the application lock would re-open the
    cross-transaction race even with this trigger in place. A
    deferred-constraint trigger would close the cross-transaction
    case at the DB layer too — backlog item, see L1 PostgreSQL role
    separation.
  - **FK cascade**: ``users.id`` is referenced by ``memberships``,
    ``refresh_tokens``, ``password_reset_tokens`` (all ON DELETE
    CASCADE) and ``audit_logs.actor_user_id`` (ON DELETE SET NULL).
    A DELETE on the last super_admin row processes:
        BEFORE DELETE on users → trigger fires → REJECTS
    The cascade never executes because the originating DELETE is
    aborted before any AFTER triggers / cascade actions fire. So no
    half-cascade state is left behind.
  - **TRUNCATE bypass**: BEFORE-row triggers do NOT fire on TRUNCATE.
    A ``TRUNCATE TABLE users`` would wipe every row including all
    super_admins. We do NOT add a TRUNCATE trigger here because the
    threat model for ``users`` is different from ``audit_logs`` —
    a wholesale wipe of users is an unrecoverable operation regardless
    (would require restore-from-backup), and the operator who runs
    TRUNCATE has already chosen the non-recoverable path. The
    audit_logs case is different because the table is append-only by
    contract; ``users`` has legitimate UPDATE/DELETE workflows.
  - **Role bypass (known residual)**: same caveat as 0012 — in the
    default install Alembic + runtime share one Postgres role
    (``trustedoss``), so an operator can DROP TRIGGER → mutate →
    re-CREATE TRIGGER. L1 (PostgreSQL role separation chore, planned
    in the backlog marathon) closes this for the runtime app.
  - **Soft-delete count**: the count predicate is
    ``is_superuser AND is_active``. Deactivating a non-last
    super_admin (count > 1) flows through; the trigger's gate only
    blocks transitions that would take the count to 0. Idempotent
    UPDATE (``SET is_superuser = TRUE`` on a row that is already
    super_admin) does not trigger the gate because OLD.is_superuser
    matches NEW.is_superuser — the OR-of-conditions short-circuits.

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    NotImplementedError. Manual rollback is
    ``DROP TRIGGER trg_last_super_admin ON users;
     DROP FUNCTION enforce_last_super_admin();``.
  - SQLSTATE 23514 (check_violation) classifies cleanly as
    ``IntegrityError`` in SQLAlchemy / asyncpg so the application
    layer can catch it identically to NOT NULL / FK violations.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION_DDL = """
CREATE OR REPLACE FUNCTION enforce_last_super_admin()
RETURNS TRIGGER AS $$
DECLARE
  was_active_super_admin BOOLEAN;
  becomes_non_super_admin BOOLEAN;
  remaining_count INTEGER;
  pass_through RECORD;
BEGIN
  -- Per the PostgreSQL docs, BEFORE-row triggers must RETURN OLD for
  -- DELETE (RETURN NEW would suppress the delete because NEW is NULL on
  -- DELETE) and RETURN NEW for UPDATE. Pin the correct pass-through
  -- value once at the top so every "allow" path reuses the same record
  -- and we never accidentally suppress a legitimate mutation.
  IF TG_OP = 'DELETE' THEN
    pass_through := OLD;
  ELSE
    pass_through := NEW;
  END IF;

  was_active_super_admin := OLD.is_superuser AND OLD.is_active;

  IF NOT was_active_super_admin THEN
    -- Row was not an active super_admin to begin with; mutation cannot
    -- reduce the protected-seat count. Allow.
    RETURN pass_through;
  END IF;

  IF TG_OP = 'DELETE' THEN
    becomes_non_super_admin := TRUE;
  ELSE
    -- TG_OP = 'UPDATE'.
    becomes_non_super_admin :=
      (NOT NEW.is_superuser) OR (NOT NEW.is_active);
  END IF;

  IF NOT becomes_non_super_admin THEN
    -- Still an active super_admin after the mutation (e.g. an UPDATE
    -- that touches an unrelated column). Allow.
    RETURN pass_through;
  END IF;

  -- Count remaining active super_admins, excluding the row being mutated.
  SELECT count(*)
    INTO remaining_count
    FROM users
   WHERE is_superuser = TRUE
     AND is_active = TRUE
     AND id <> OLD.id;

  IF remaining_count = 0 THEN
    RAISE EXCEPTION 'last active super_admin cannot be removed or demoted (TG_OP=%)', TG_OP
      USING ERRCODE = '23514';
  END IF;

  RETURN pass_through;
END;
$$ LANGUAGE plpgsql;
""".strip()


_TRIGGER_DDL = """
CREATE TRIGGER trg_last_super_admin
BEFORE UPDATE OR DELETE ON users
FOR EACH ROW EXECUTE FUNCTION enforce_last_super_admin();
""".strip()


def upgrade() -> None:
    op.execute(_FUNCTION_DDL)
    op.execute(_TRIGGER_DDL)


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
