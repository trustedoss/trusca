"""user anonymisation: the two-person request, and a narrow hole in the audit trigger

Revision ID: 0080
Revises: 0079
Create Date: 2026-09-04

Kind: schema (new table, one function, one trigger-function replacement)
Forward-only: yes

What:
  - Create ``user_anonymisation_requests`` (see the model for column meanings).
  - Replace ``audit_logs_prevent_mutation()`` so that ``ip`` and
    ``user_agent`` may be nulled while a session flag is set, and nothing else
    changes.
  - Create ``audit_logs_scrub_pii(uuid)``, ``SECURITY DEFINER``, the only
    caller that sets that flag.
  - GRANT the table to ``trustedoss_app``. NOT the function.

Why the trigger is replaced rather than dropped
-----------------------------------------------
0012 made ``audit_logs`` append-only, and it is right: an audit trail somebody
can edit is not evidence. But an anonymisation request has to remove the
subject's IP address and browser string from their own audit rows, and those
two sit inside the nine content columns 0012 pins.

The obvious move, dropping the trigger for the duration and putting it back,
is the one not taken. For the length of that window the table is unprotected
against everything, not just the intended write, and nothing records what
passed through. So the exception is built into the trigger instead, where it
can be described precisely:

  - it requires the session to be running as the table's owner, which
    ``SECURITY DEFINER`` arranges for calls through the function and which no
    other role can arrange for itself;
  - it also requires ``trusca.audit_scrub`` on the session, which dies with
    the transaction, so owner-role maintenance work does not qualify by
    accident;
  - it permits exactly two columns to change, and only toward NULL;
  - every other column is compared as before, so a statement that also touched
    ``diff`` or ``action`` is refused even with the flag set.

The function and the trigger therefore narrow each other. Widening the hole
takes an edit to both, in a migration, in review.

The role check is load-bearing and was added after testing rather than
designed in. With only the session flag, the application role could call
``set_config`` itself and null its own ``ip`` and ``user_agent`` directly,
because a custom GUC is settable by any role. That is precisely the tampering
0012 exists to prevent, and it was reachable by anything that could execute
SQL as the app.

Why the function is SECURITY DEFINER and not granted to the app
---------------------------------------------------------------
``trustedoss_app`` is the role the API and the workers run as, and it is the
role an SQL injection would run as. It gets no ``EXECUTE`` here.

That is a deliberate constraint on how the erasure runs, not an oversight, so
the shape is worth stating before somebody resolves it by granting EXECUTE and
reopening what the role check above closes. ``docker-compose.yml`` never puts
``DATABASE_URL_OWNER`` into a runtime container: alembic is invoked with an
explicit override from ``install.sh`` / ``upgrade.sh`` so DDL runs as the owner
exactly once and exits, and the comment there says why, "a runtime RCE cannot
DROP TRIGGER on audit_logs". Handing the app EXECUTE on a function that edits
audit rows would undo that in one line.

So the request and the approval live in the API, where two super-admins can
reach them and where every step is audited, and the erasure itself is an
operator command run with the owner DSN, alongside ``create_super_admin`` and
the other scripts in ``apps/backend/scripts/``. It reads the approved request,
performs the scrub, and marks the request executed. An operator who can run it
already has the owner credentials, so it grants no authority they lacked, and
the function's own approval check means those credentials still cannot erase
anything two super-admins have not agreed to.

Notes:
  - The FKs on the new table are ``RESTRICT``, unlike every other user
    reference in this schema. The rows record who authorised an irreversible
    act; a NULLed approver would turn that into nobody. See the model.
  - No index on ``subject_user_id`` beyond the partial unique one. The table
    grows by one row per request, including cancelled and expired ones, so a
    subject can accumulate several; it is still bounded by how often a human
    opens a request, which is not a curve worth another index.
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises NotImplementedError.
    Manual rollback of the trigger half is to re-apply 0012's function body.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


# The 0012 body with one branch added. Kept whole rather than patched so the
# rule a reader has to check is in one place.
_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_logs_prevent_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'TRUNCATE' OR TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=%)', TG_OP
      USING ERRCODE = '23000';
  END IF;

  -- TG_OP = 'UPDATE' from here on.

  -- ER32 exception. A user anonymisation must clear the subject's client
  -- details from their own audit rows, and both live inside the immutable
  -- set below. The exception is deliberately the narrowest shape that does
  -- the job: the session flag is set only by audit_logs_scrub_pii() and dies
  -- with its transaction, the two columns may only move toward NULL, and
  -- every other column must be unchanged. A statement that also touched
  -- diff, action or any other content column falls through to the checks
  -- below and is refused exactly as before.
  IF current_setting('trusca.audit_scrub', true) = 'on'
     -- The flag alone is not a gate. Any role with UPDATE on this table can
     -- call set_config for a custom GUC, so a session flag by itself would
     -- let the application role (and anything that reached SQL execution
     -- through it) erase its own client details from the audit trail. This
     -- is the half that cannot be forged: SECURITY DEFINER runs the scrub
     -- function as its owner, which is the table's owner, and the app role
     -- is not a member of it. Measured before adding this line: the app role
     -- set the flag itself and the UPDATE succeeded.
     AND pg_has_role(current_user,
                     (SELECT relowner FROM pg_class
                       WHERE oid = 'public.audit_logs'::regclass), 'MEMBER')
     AND NEW.ip IS NULL AND NEW.user_agent IS NULL
     -- Everything except the two columns being cleared must be untouched.
     -- Expressed by subtraction rather than by listing the columns to
     -- protect: a list is correct on the day it is written and silently
     -- wrong the day somebody adds a column, because the new column would
     -- be in neither the exception's comparison nor anyone's attention, and
     -- would become quietly editable whenever the flag is set. Subtracting
     -- means a new column is protected the moment it exists.
     AND to_jsonb(OLD) - 'ip' - 'user_agent'
         IS NOT DISTINCT FROM
         to_jsonb(NEW) - 'ip' - 'user_agent'
  THEN
    RETURN NEW;
  END IF;

  -- Strict: every column is immutable except the two FK columns handled
  -- below, which Postgres itself rewrites on a parent delete. Stated by
  -- subtraction for the same reason as the exception above: a list of
  -- columns to protect is correct on the day it is written, and the day
  -- somebody adds a column that column is protected by nobody.
  --
  -- This compares values, not intent, so an UPDATE that writes a column its
  -- existing value is allowed through: it changed nothing. Worth knowing
  -- before testing this trigger. Aim a forged scrub at a row whose ip is
  -- already NULL and it reports UPDATE 1, which reads exactly like the
  -- exception being bypassed and is not. Use a row whose values are still
  -- set, and assert afterwards that they survived.
  IF to_jsonb(OLD) - 'actor_user_id' - 'team_id'
     IS DISTINCT FROM
     to_jsonb(NEW) - 'actor_user_id' - 'team_id'
  THEN
    RAISE EXCEPTION 'audit_logs is append-only (TG_OP=UPDATE on content column)'
      USING ERRCODE = '23000',
            HINT = 'This guard covers every column, including ones added '
                   'after it was written, so a new column is immutable by '
                   'default. If a column genuinely needs to be updatable, '
                   'that is a decision to make in a migration that amends '
                   'this trigger, not a bug in the calling code.';
  END IF;

  -- actor_user_id and team_id are FK columns with ON DELETE SET NULL on
  -- their parent tables. When a User or Team row is removed, Postgres
  -- propagates the cascade by UPDATEing referencing audit_logs rows to
  -- NULL their FK column. Allow that exact transition (any to NULL) but
  -- refuse any other change: rotating to a different non-NULL id would
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
$$ LANGUAGE plpgsql SET search_path = pg_catalog, public;
""".strip()


# The only thing that may set the flag. Two columns named, nothing else
# reachable from here: widening it means editing this body in a migration.
#
# ``SET search_path`` is pinned, and every table it touches is schema
# qualified. A SECURITY DEFINER function runs with the owner's rights but the
# CALLER's search path, so without this a caller could create its own
# ``audit_logs`` in a schema it controls, put that schema first, and have the
# function operate on the decoy while believing it had the real table. That is
# the same shape as the flag hole above: a boundary that depended on a value
# the other side could set.
#
# It also verifies the two-person approval itself. EXECUTE on this function
# would otherwise be sufficient authority to erase anyone's audit details,
# which would make the approval table decorative.
_SCRUB_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_logs_scrub_pii(target_actor uuid)
RETURNS integer AS $$
DECLARE
  touched integer;
  approved integer;
BEGIN
  IF target_actor IS NULL THEN
    RAISE EXCEPTION 'audit_logs_scrub_pii requires an actor id'
      USING ERRCODE = '22004';
  END IF;

  -- The function checks the authorisation itself rather than trusting its
  -- caller to have done so. Without this, holding EXECUTE would BE the
  -- authorisation: anyone who could call it could erase any user's client
  -- details, and the two-person approval in front of it would be a formality
  -- the database never checked.
  SELECT count(*) INTO approved
    FROM public.user_anonymisation_requests
   WHERE subject_user_id = target_actor
     -- 'approved' only. Including 'executed' would leave the door open for
     -- good: after one erasure, any audit row that ever appeared for that
     -- subject could be scrubbed again with no new decision by anyone. The
     -- operator command scrubs before it marks the request executed, so the
     -- narrower condition is the one it actually needs.
     AND state = 'approved'
     AND approved_by_user_id IS NOT NULL;

  IF approved = 0 THEN
    RAISE EXCEPTION
      'audit_logs_scrub_pii: no approved anonymisation request for %',
      target_actor
      USING ERRCODE = '42501';
  END IF;

  PERFORM set_config('trusca.audit_scrub', 'on', true);

  UPDATE public.audit_logs
     SET ip = NULL, user_agent = NULL
   WHERE actor_user_id = target_actor
     AND (ip IS NOT NULL OR user_agent IS NOT NULL);
  GET DIAGNOSTICS touched = ROW_COUNT;

  -- Off again inside the same transaction, so nothing after this call in the
  -- same statement batch inherits the exception.
  PERFORM set_config('trusca.audit_scrub', 'off', true);
  RETURN touched;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public;
""".strip()


def upgrade() -> None:
    op.create_table(
        "user_anonymisation_requests",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "subject_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approved_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'approved', 'executed', 'cancelled', 'expired')",
            name="ck_user_anonymisation_requests_state",
        ),
        sa.CheckConstraint(
            "approved_by_user_id IS NULL OR ("
            "approved_by_user_id <> requested_by_user_id "
            "AND approved_by_user_id <> subject_user_id)",
            name="ck_user_anonymisation_requests_distinct_parties",
        ),
        sa.CheckConstraint(
            "requested_by_user_id <> subject_user_id",
            name="ck_user_anonymisation_requests_not_self",
        ),
    )
    op.create_index(
        "uq_user_anonymisation_requests_open",
        "user_anonymisation_requests",
        ["subject_user_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('pending', 'approved')"),
    )
    op.create_index(
        "ix_user_anonymisation_requests_state",
        "user_anonymisation_requests",
        ["state"],
    )

    op.execute(_TRIGGER_FUNCTION)
    op.execute(_SCRUB_FUNCTION)

    # The app role reads and writes the request rows. It does NOT get EXECUTE
    # on the scrub function: see the module docstring.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trustedoss_app') THEN
                GRANT SELECT, INSERT, UPDATE
                    ON user_anonymisation_requests TO trustedoss_app;
                REVOKE ALL ON FUNCTION audit_logs_scrub_pii(uuid) FROM PUBLIC;
                REVOKE ALL ON FUNCTION audit_logs_scrub_pii(uuid) FROM trustedoss_app;
            ELSE
                RAISE NOTICE 'trustedoss_app role not found - '
                    'single-role legacy mode (no-op)';
            END IF;
        END
        $$;
        """
    )
    # Even without the app role, PUBLIC must not hold the default EXECUTE that
    # Postgres grants on every new function.
    op.execute("REVOKE ALL ON FUNCTION audit_logs_scrub_pii(uuid) FROM PUBLIC;")


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
