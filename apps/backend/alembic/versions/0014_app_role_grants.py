"""trustedoss_app role grants — DML-only on audit_logs (depth-in-defence to 0012 trigger)

Revision ID: 0014
Revises: 0013
Created: 2026-05-11

Phase: post-walkthrough hardening (Marathon bundle 8 / L1)
PR: chore/postgres-role-separation
Kind: schema (DDL-only, no data migration)
Forward-only: yes

What:
  - Detect whether the optional ``trustedoss_app`` runtime role exists.
    When present, the migration:
      * GRANTs ``SELECT, INSERT, UPDATE, DELETE`` on every table in
        the public schema (current + future) so normal app traffic
        works as today.
      * REVOKEs ``UPDATE, DELETE, TRUNCATE`` on ``audit_logs``
        specifically. Combined with the immutable trigger from
        migration 0012, this gives the audit table a runtime
        contract of "INSERT-only — no rotation, no edit, no wipe"
        even if an attacker drops the trigger.
      * GRANTs only ``SELECT, INSERT`` on ``audit_logs``.
      * Future tables auto-inherit the SELECT/INSERT/UPDATE/DELETE
        grant via ``ALTER DEFAULT PRIVILEGES``.

Why (Marathon bundle 8 / L1, post-walkthrough):
  - Migration 0012 added an audit_logs append-only trigger but called
    out a residual: in the default install both alembic AND the runtime
    share one Postgres role (``trustedoss``). That role can
    ``DROP TRIGGER ... → UPDATE audit_logs → CREATE TRIGGER ...`` to
    bypass the immutability guarantee.
  - Splitting the runtime into ``trustedoss_app`` (DML-only on
    ``audit_logs``, no DDL on any table) closes that bypass: the
    runtime can't drop the trigger because it doesn't own the table,
    and even without the trigger the REVOKE prevents UPDATE/DELETE/
    TRUNCATE.
  - ``trustedoss_owner`` (the existing ``trustedoss`` role, used for
    migrations) keeps full DDL — install.sh / upgrade.sh runs alembic
    as that role.

Backward compat (dev / CI without the split):
  - When the ``trustedoss_app`` role does NOT exist, this migration
    is a no-op. Existing single-role deployments continue to work
    exactly as before. install.sh / upgrade.sh in marathon bundle 8
    creates the role + provisions the password + flips the runtime
    env vars (DATABASE_URL_APP); operators who skip those steps
    keep the legacy single-role posture. Re-running this migration
    AFTER the role is created applies the GRANTs / REVOKEs.

Adversarial reasoning (memory ``feedback_security_reviewer_db_cascade_blind_spot``):
  - **TRUNCATE bypass**: REVOKEd from trustedoss_app. The 0012
    BEFORE TRUNCATE statement-level trigger is the secondary line of
    defence; this REVOKE makes TRUNCATE fail with a permission denied
    BEFORE the trigger even fires.
  - **DROP TRIGGER bypass**: trustedoss_app does not own ``audit_logs``
    (the owner role does), so ``DROP TRIGGER ... ON audit_logs``
    returns ``permission denied for relation audit_logs``.
  - **Cross-table DDL**: ``REVOKE CREATE ON SCHEMA public FROM
    trustedoss_app`` would also block ``CREATE TABLE`` from the runtime.
    We do NOT do that here because some tests rely on the runtime role
    being able to create temporary tables; production hardening can
    add that revoke in a follow-up if the threat model demands it
    (Phase 8 SaaS hardening backlog).

Notes:
  - Forward-only per CLAUDE.md §6: ``downgrade()`` raises
    NotImplementedError. Manual rollback if a critical incident
    demands: connect as the owner role and run
    ``GRANT UPDATE, DELETE, TRUNCATE ON audit_logs TO trustedoss_app;``.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_GRANT_DDL = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'trustedoss_app') THEN
        -- 1. Schema-level usage so the role can resolve table refs.
        --    Explicit REVOKE CREATE so the runtime cannot create new
        --    tables / functions / types — defence-in-depth on Postgres
        --    versions where the implicit GRANT CREATE TO PUBLIC is still
        --    in place (PG < 15). On PG 15+ this is a no-op but keeps
        --    the contract obvious to future readers.
        GRANT USAGE ON SCHEMA public TO trustedoss_app;
        REVOKE CREATE ON SCHEMA public FROM trustedoss_app;

        -- 2. Default DML on every existing table (broad grant).
        GRANT SELECT, INSERT, UPDATE, DELETE
            ON ALL TABLES IN SCHEMA public TO trustedoss_app;

        -- 3. Tighten audit_logs: INSERT + SELECT only. We REVOKE ALL
        --    first then GRANT explicitly so the resulting ACL is two
        --    clean entries instead of "ALL minus three" (security-
        --    reviewer Low #1: the composite ACL was confusing for
        --    operators reading pg_class_aclitem during forensics).
        --    Even if the immutable trigger (migration 0012) is
        --    dropped, the role lacks the privilege to mutate or wipe.
        REVOKE ALL ON audit_logs FROM trustedoss_app;
        GRANT SELECT, INSERT ON audit_logs TO trustedoss_app;

        -- 4. Sequences (SERIAL / IDENTITY) need USAGE to consume nextval.
        --    asyncpg's UUID-PK pattern doesn't use sequences, but if a
        --    future table does this prevents an INSERT-permission-denied
        --    surprise.
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO trustedoss_app;

        -- 5. Default privileges for FUTURE tables — SELECT + INSERT only
        --    (security-reviewer High #3: deny-by-default for mutation).
        --    A future migration adding a table that needs UPDATE/DELETE
        --    for the runtime must explicitly GRANT them in its own
        --    upgrade body. This keeps the audit-style "append-only"
        --    posture as the default for any future audit_* table that
        --    might be added (rather than relying on the L1 author to
        --    remember to REVOKE per-table).
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT SELECT, INSERT ON TABLES TO trustedoss_app;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
            GRANT USAGE, SELECT ON SEQUENCES TO trustedoss_app;

        RAISE NOTICE
            'trustedoss_app role: DML grants applied; '
            'audit_logs INSERT/SELECT only; CREATE on public revoked';
    ELSE
        RAISE NOTICE 'trustedoss_app role not found — single-role legacy mode (no-op)';
    END IF;
END $$;
""".strip()


def upgrade() -> None:
    op.execute(_GRANT_DDL)


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported (forward-only policy)")
