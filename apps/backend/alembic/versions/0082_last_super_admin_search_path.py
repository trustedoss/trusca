"""last super admin guard: count the real users table, not one the caller made

Revision ID: 0082
Revises: 0081
Create Date: 2026-09-04

Kind: schema (one function replacement)
Forward-only: yes

What was wrong
--------------
``enforce_last_super_admin()`` from 0013 counted the remaining active super
admins with an unqualified ``FROM users`` and no pinned ``search_path``. A
trigger function runs with the caller's search path, PostgreSQL looks in the
temp schema first, and TEMP is granted to PUBLIC. So a caller could write

    CREATE TEMP TABLE users (id uuid, is_superuser boolean, is_active boolean);
    INSERT INTO users VALUES (gen_random_uuid(), TRUE, TRUE);
    UPDATE public.users SET is_superuser = FALSE WHERE id = <the last one>;

and the guard would count rows in the caller's own table, find company, and
allow the demotion. Measured on a fresh database: the update succeeded and
the deployment was left with zero active super admins, a state nobody can
administer and that needs the owner credential to undo.

This needed no extra privilege. The application role already holds UPDATE and
DELETE on ``users`` because that is how it manages accounts, so anything that
reached SQL execution through the running portal could do it.

The fix is the same pair 0080 applied to the audit trigger: pin the function's
``search_path`` and name the table with its schema. The logic is untouched.

``pg_temp`` is listed last and that is the part worth reading twice. Pinning
the path does NOT demote the temporary schema by omission: PostgreSQL
searches it first for a relation unless the path names it explicitly.
``SET search_path = pg_catalog, public`` therefore leaves this defect exactly
where it was. Measured on 17.2 with two otherwise identical functions, one
pinned without ``pg_temp`` and one with: the first read a caller's temp table
of the same name, the second read the real one.

Each half stops the attack alone, which was checked rather than assumed:
qualification without the pinned path refuses, the pinned path without
qualification refuses, and removing both lets the demotion through.

If you are writing a database function
-------------------------------------
Copy the header from here or from 0080, not from 0013. Those two are the
repository's first functions to pin a search path at all, so whatever they do
is what the next one will do. Two things carry:

    $$ LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp;

and every table named with its schema. ``pg_temp`` last is not decoration and
its absence is invisible: a function pinned to ``pg_catalog, public`` looks
correct, passes a shadow-table test as long as the references are qualified,
and resolves an unqualified reference to the caller's temp table the first
time somebody adds one.

Why a function replacement and not a new trigger
------------------------------------------------
The trigger definition in 0013 is correct; only the function body resolved a
name loosely. ``CREATE OR REPLACE FUNCTION`` keeps the existing trigger
pointing at the corrected body, so there is no window in which the table has
no guard.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FUNCTION = """
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
    FROM public.users
   WHERE is_superuser = TRUE
     AND is_active = TRUE
     AND id <> OLD.id;

  IF remaining_count = 0 THEN
    RAISE EXCEPTION 'last active super_admin cannot be removed or demoted (TG_OP=%)', TG_OP
      USING ERRCODE = '23514';
  END IF;

  RETURN pass_through;
END;
$$ LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp;
"""


def upgrade() -> None:
    op.execute(_FUNCTION)


def downgrade() -> None:
    # Forward-only per CLAUDE.md §6. Restoring the previous body would put
    # back a guard that a temp table defeats.
    raise NotImplementedError("0013's unqualified function body is not restored")
