#!/bin/sh
# TrustedOSS Portal — backend (production) container entrypoint.
#
# Purpose (v2.1): make "from clone to running" / "no-clone one-command install"
# truly one command by applying Alembic migrations automatically when the
# backend container starts — removing the manual
#   docker-compose ... exec backend alembic upgrade head
# step from the install path. CMD (uvicorn) is exec'd at the end so tini stays
# PID 1 and signals propagate cleanly.
#
# POSIX sh on purpose: this runs under whatever the runtime base ships
# (python:3.12.7-slim => bash present, but alpine images only ship ash). No
# bashisms — `set -eu`, `[ ... ]`, `exec "$@"` only. shellcheck-clean.
#
# ---------------------------------------------------------------------------
# Toggle — AUTO_MIGRATE (default: true)
# ---------------------------------------------------------------------------
# Operators who manage the schema out-of-band (a separate migration job, a
# DBA-run alembic, or a read-replica-only container) set AUTO_MIGRATE=false in
# .env to skip the upgrade entirely. The toggle is case-folded and accepts the
# usual truthy spellings (true / 1 / yes / on); the literal "false" (and the
# usual falsy spellings) disables it. Any OTHER non-empty value is treated as
# a typo: we log a WARNING and SKIP migration so the mistake is visible rather
# than silently doing the wrong thing (L1toggle).
#
# ---------------------------------------------------------------------------
# Forward-only (CLAUDE.md migration policy)
# ---------------------------------------------------------------------------
# This entrypoint ONLY ever runs `alembic upgrade head`. It never downgrades.
# Downgrades are not supported by the project (downgrade() is a no-op), so the
# automated path cannot move the schema backwards.
#
# ---------------------------------------------------------------------------
# Reachability vs. migration — fail-fast on permanent errors (H2)
# ---------------------------------------------------------------------------
# The retry loop ONLY guards a transient "DB not ready yet" (warming up,
# restarting). It probes reachability with `alembic current`, which needs just
# a SELECT (it reads alembic_version) — no DDL, no schema change — so it
# succeeds the moment Postgres accepts connections and authenticates. Once the
# DB is reachable we run `alembic upgrade head` EXACTLY ONCE and, on failure,
# `exit 1` immediately: a migration failure against a reachable DB (bad
# credentials for DDL, a broken revision, a half-applied schema) is never
# transient, and `restart: unless-stopped` would otherwise turn it into an
# infinite crash-loop. Fast-fail surfaces the real error in the first restart.
#
# ---------------------------------------------------------------------------
# !!! MULTI-REPLICA WARNING (read before reusing this on Kubernetes) !!!
# ---------------------------------------------------------------------------
# docker-compose runs the backend as a SINGLE container by default, so exactly
# one process runs `alembic upgrade head`. If you `docker-compose up
# --scale backend=N`, OR run on Kubernetes with replicas > 1, multiple
# entrypoints would run the upgrade concurrently. That race is made safe by a
# Postgres advisory lock taken inside alembic/env.py (run_migrations_online):
# the second runner blocks until the first finishes, then finds the schema
# already at HEAD and no-ops. So concurrent starts are correct here. The Helm
# path STILL prefers a single pre-install/pre-upgrade Job with
# AUTO_MIGRATE=false on the pods (post-GA roadmap §3) — the lock is a safety
# net, not a license to migrate from every pod.
#
# ---------------------------------------------------------------------------
# Owner vs. app DB role (Marathon bundle 8 / L1) — see hand-off note in report
# ---------------------------------------------------------------------------
# In the role-separated deployment, runtime containers see only the DML-only
# DATABASE_URL_APP, while DDL (alembic) needs the owner role. install.sh /
# upgrade.sh run alembic with an explicit `-e DATABASE_URL=$DATABASE_URL_OWNER`
# override exactly once. This entrypoint runs alembic with whatever DATABASE_URL
# the container has — which under L1 is the APP role and will FAIL on DDL. So:
# on an L1 role-separated stack, keep AUTO_MIGRATE=false and let install.sh /
# upgrade.sh own the owner-role migration. install.sh DETECTS L1 and writes
# AUTO_MIGRATE=false into .env automatically (H1); on the no-clone path the
# operator sets it. If it is left true on an L1 stack, the H2 fast-fail above
# surfaces the DDL permission error on the first attempt instead of looping.
# On a single-role stack (the default .env, where DATABASE_URL is the owning
# role), AUTO_MIGRATE=true just works.

set -eu

log() { printf '%s entrypoint: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

# ---------------------------------------------------------------------------
# Signal handling during migration (L2signal)
# ---------------------------------------------------------------------------
# A large migration can run for minutes. If the operator `docker stop`s the
# container mid-migration, tini (PID 1) forwards SIGTERM to this script. We
# trap it so we exit promptly and predictably with 143 (128 + SIGTERM) rather
# than leaving the trap-less default. After we `exec "$@"` the trap is gone and
# signals reach uvicorn directly (its own graceful shutdown takes over).
on_term() {
  log "received SIGTERM/SIGINT — aborting before/while migrating; exiting 143"
  exit 143
}
trap on_term TERM INT

# ---------------------------------------------------------------------------
# AUTO_MIGRATE toggle — case-folded, typo-visible (L1toggle)
# ---------------------------------------------------------------------------
# Unset OR empty falls back to the default "true" here, so the case arms
# below never see an empty value — default is enabled (L1toggle).
auto_migrate_raw="${AUTO_MIGRATE:-true}"
# POSIX lower-case fold via tr (portable across ash/bash).
auto_migrate=$(printf '%s' "$auto_migrate_raw" | tr '[:upper:]' '[:lower:]')
migrate=0
case "$auto_migrate" in
  true|1|yes|on)   migrate=1 ;;
  false|0|no|off)  migrate=0 ;;
  *)
    log "WARNING: AUTO_MIGRATE='${auto_migrate_raw}' is not a recognised boolean"
    log "         (expected one of: true/1/yes/on or false/0/no/off)."
    log "         Treating it as DISABLED and SKIPPING migrations — fix the typo"
    log "         in .env if you meant to enable automatic migration."
    migrate=0
    ;;
esac

if [ "$migrate" -eq 1 ]; then
  log "AUTO_MIGRATE='${auto_migrate_raw}' — auto-migration enabled"

  # ---- Phase 1: DB reachability (bounded retry, transient-only) ----------
  # `alembic current` issues a read-only SELECT against alembic_version. It
  # connects + authenticates but does NOT change the schema, so success here
  # means "the DB is up and we can talk to it". This is the ONLY thing we
  # retry — a warming-up / restarting Postgres. A reachable-but-failing
  # migration is handled in Phase 2 with an immediate exit.
  attempts=10        # max reachability probes
  delay=3            # seconds between probes
  i=1
  db_ready=0
  while [ "$i" -le "$attempts" ]; do
    if alembic current >/dev/null 2>&1; then
      db_ready=1
      log "database reachable (alembic current ok) after attempt ${i}/${attempts}"
      break
    fi
    if [ "$i" -lt "$attempts" ]; then
      log "database not reachable yet (attempt ${i}/${attempts}); retrying in ${delay}s"
      sleep "$delay"
    fi
    i=$((i + 1))
  done

  if [ "$db_ready" -ne 1 ]; then
    log "ERROR: database did not become reachable after ${attempts} attempts."
    log "       The DB may be down or DATABASE_URL host/port/credentials are"
    log "       wrong. Inspect 'docker-compose logs postgres'. (No password is"
    log "       printed here or in the alembic traceback — hide_parameters=True.)"
    exit 1
  fi

  # ---- Phase 2: apply migrations ONCE, fail-fast on any error (H2) --------
  # The DB is reachable. Any failure now is permanent (broken revision, DDL
  # permission denied under an L1 app-role DSN, half-applied schema). Do NOT
  # retry — exit 1 so `restart: unless-stopped` does not crash-loop on a
  # permanent error. Concurrency across replicas is serialised by the
  # pg_advisory_lock in alembic/env.py, so a single attempt is also race-safe.
  log "applying Alembic migrations (alembic upgrade head)"
  if alembic upgrade head; then
    log "migrations applied — schema is at HEAD"
  else
    log "ERROR: 'alembic upgrade head' failed against a reachable database."
    log "       This is NOT transient — a broken revision, a half-applied"
    log "       schema, or insufficient DDL privileges (e.g. an L1 app-role"
    log "       DSN that lacks CREATE/ALTER). Read the alembic traceback above"
    log "       (DSN passwords are masked via hide_parameters=True). On an L1"
    log "       role-separated stack, set AUTO_MIGRATE=false and migrate as the"
    log "       owner role (install.sh / upgrade.sh do this). Exiting 1."
    exit 1
  fi
else
  log "AUTO_MIGRATE='${auto_migrate_raw}' — migrations skipped; schema is managed out-of-band"
fi

# Hand off to CMD (uvicorn ...). exec replaces this shell so tini (PID 1)
# signals reach uvicorn directly — clean SIGTERM on `docker stop`. The TERM
# trap above is dropped by exec; uvicorn owns graceful shutdown from here.
log "starting application: $*"
exec "$@"
