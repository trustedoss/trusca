#!/usr/bin/env bash
# Marathon bundle 8 (L1) — Postgres first-boot role provisioning.
#
# This script lives at /docker-entrypoint-initdb.d/10-trustedoss-app-role.sh
# inside the postgres:17.2-alpine container. The official entrypoint runs
# everything in that directory ONLY when the data volume is empty (fresh
# install) — re-running compose with an existing volume is a no-op.
#
# Purpose:
#   - Create the ``trustedoss_app`` runtime role with the password
#     supplied via POSTGRES_APP_PASSWORD. install.sh generates the
#     password and wires both DATABASE_URL_OWNER and DATABASE_URL_APP
#     into .env.
#   - The role is created with LOGIN INHERIT but NO inherited admin
#     privileges. Migration 0014 (alembic) applies the GRANTs/REVOKEs
#     after the role exists.
#
# What this script does NOT do:
#   - Apply GRANTs / REVOKEs — those live in Alembic migration 0014 so
#     they replay deterministically across all environments and survive
#     volume wipes.
#   - Set the role's password on subsequent boots — Postgres only runs
#     this file once. Operators rotating the app password must
#     ``ALTER ROLE trustedoss_app WITH PASSWORD '...'`` by hand and
#     update .env in lockstep.

set -euo pipefail

if [[ -z "${POSTGRES_APP_PASSWORD:-}" ]]; then
  echo "[trustedoss-app-role] POSTGRES_APP_PASSWORD unset — skipping L1 role provisioning"
  echo "[trustedoss-app-role] (single-role legacy mode; alembic 0014 will be a no-op)"
  exit 0
fi

app_user="${POSTGRES_APP_USER:-trustedoss_app}"

# Validate the role name to prevent SQL injection from a tampered env.
# Postgres role names allow letters / digits / underscore.
if [[ ! "$app_user" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
  echo "[trustedoss-app-role] illegal POSTGRES_APP_USER: $app_user" >&2
  exit 1
fi

# Password injection safety (security-reviewer Medium M4): pass the
# password through psql's ``--variable`` so the quoting happens inside
# psql's own lexer (``:'name'`` syntax SQL-quotes the variable),
# never via shell heredoc expansion. A password containing a single
# quote or backslash that previously would have broken the SQL parse
# is now handled correctly. The user / db / port flow through the
# same ``--variable`` channel for symmetry.
#
# The init script still runs as the OS-level postgres superuser (the
# image entrypoint convention); psql `--username "${POSTGRES_USER}"`
# uses that connection.
psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" \
     --set ON_ERROR_STOP=1 \
     --variable=app_user="${app_user}" \
     --variable=app_password="${POSTGRES_APP_PASSWORD}" <<-'SQL'
	-- :'app_user' / :'app_password' are SQL-quoted by psql before the
	-- query reaches Postgres. ``format(%I, %L, ...)`` then re-quotes for
	-- the EXECUTE on the identifier / literal — double-quote defence.
	DO $$
	DECLARE
	  v_user TEXT := :'app_user';
	  v_pass TEXT := :'app_password';
	BEGIN
	  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_user) THEN
	    EXECUTE format(
	      'CREATE ROLE %I WITH LOGIN INHERIT PASSWORD %L',
	      v_user,
	      v_pass
	    );
	    RAISE NOTICE 'created role %', v_user;
	  ELSE
	    RAISE NOTICE 'role % already exists — skipping CREATE', v_user;
	  END IF;
	END
	$$;
SQL

echo "[trustedoss-app-role] provisioned ${app_user} (alembic 0014 will apply GRANTs)"
