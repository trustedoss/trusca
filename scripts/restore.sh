#!/usr/bin/env bash
# TrustedOSS Portal — restore from a backup directory.
#
# Usage:
#   bash scripts/restore.sh backups/2026-05-08-143000
#
# Steps:
#   1. Stop application services (backend / frontend / worker / beat).
#      Postgres + Redis stay up — we restore in place.
#   2. Restore PostgreSQL via pg_dump's clean-and-load output.
#   3. Restore the workspace tar (if present).
#   4. Restart application services.
#   5. Verify alembic head matches the manifest.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

ok()    { printf "${GREEN}✓${RESET} %s\n" "$1"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$1"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }
title() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

# Marathon bundle 4 (R / M5) — accept ``--confirm`` flag to skip the
# interactive prompt. Programmatic callers (CI / install-uat workflow)
# pass ``--confirm`` instead of ``BACKUP_RESTORE_CONFIRM=yes`` so the
# intent is visible in argv (``ps`` output) rather than buried in env.
# The legacy env var is no longer honoured — operators who automated
# against it now see a WARNING (below) and fall through to the
# interactive prompt unless they pass ``--confirm``.
CONFIRM=0
BACKUP_DIR=""
for arg in "$@"; do
  case "$arg" in
    --confirm)
      CONFIRM=1
      ;;
    --help|-h)
      printf 'Usage: bash scripts/restore.sh [--confirm] <backup-dir>\n'
      exit 0
      ;;
    -*)
      fail "unknown flag: $arg (only --confirm / --help supported)"
      ;;
    *)
      [[ -z "$BACKUP_DIR" ]] || fail "more than one backup-dir given"
      BACKUP_DIR="$arg"
      ;;
  esac
done
[[ -n "$BACKUP_DIR" ]] || fail "usage: bash scripts/restore.sh [--confirm] <backup-dir>"
[[ -d "$BACKUP_DIR" ]] || fail "no such directory: $BACKUP_DIR"
[[ -f "$BACKUP_DIR/postgres.sql.gz" ]] || fail "missing $BACKUP_DIR/postgres.sql.gz"

command -v docker-compose >/dev/null 2>&1 || fail "docker-compose (V1) is required."

# Migration warning for operators still wired against the legacy env var.
if [[ -n "${BACKUP_RESTORE_CONFIRM:-}" ]]; then
  warn "BACKUP_RESTORE_CONFIRM env var is no longer honoured (Marathon bundle 4)."
  warn "Pass --confirm as an argv flag instead: bash scripts/restore.sh --confirm <dir>"
fi

[[ -f .env ]] || fail ".env not found — restore requires the same env that took the backup."
# shellcheck disable=SC1091
set -a; . ./.env; set +a
WORKSPACE_HOST_PATH=${WORKSPACE_HOST_PATH:-/opt/trustedoss/workspace}

# Confirm with the operator before destructive ops. The ``--confirm``
# flag skips the interactive prompt for automated callers (CI / Celery
# wrapper); direct CLI use without the flag still pauses for [y/N].
title "About to restore from $BACKUP_DIR"
warn "This will:"
warn "  - REPLACE the current database content"
warn "  - REPLACE $WORKSPACE_HOST_PATH (if workspace.tar.gz present)"
if [[ $CONFIRM -eq 1 ]]; then
  ok "--confirm flag passed — skipping interactive prompt"
else
  read -r -p "Continue? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || fail "aborted"
fi

# ---------------------------------------------------------------------------
# 1. Stop application services (DB + Redis remain up)
# ---------------------------------------------------------------------------
title "Stopping application containers"
docker-compose -f docker-compose.yml stop backend frontend worker beat 2>/dev/null || true
ok "application stopped"

# ---------------------------------------------------------------------------
# 2. PostgreSQL restore
# ---------------------------------------------------------------------------
title "Restoring PostgreSQL"
gunzip -c "$BACKUP_DIR/postgres.sql.gz" \
  | docker-compose -f docker-compose.yml exec -T postgres psql -U trustedoss -d trustedoss
ok "database restored"

# ---------------------------------------------------------------------------
# 3. Workspace restore (optional)
# ---------------------------------------------------------------------------
if [[ -f "$BACKUP_DIR/workspace.tar.gz" ]]; then
  title "Restoring workspace"
  rm -rf "${WORKSPACE_HOST_PATH:?must-be-set}"
  mkdir -p "$(dirname "$WORKSPACE_HOST_PATH")"
  tar -C "$(dirname "$WORKSPACE_HOST_PATH")" -xzf "$BACKUP_DIR/workspace.tar.gz"
  ok "workspace restored"
else
  warn "no workspace.tar.gz in backup — skipping workspace restore"
fi

# ---------------------------------------------------------------------------
# 4. Restart application
# ---------------------------------------------------------------------------
title "Restarting application"
docker-compose -f docker-compose.yml up -d
ok "application restarted"

# ---------------------------------------------------------------------------
# 5. Manifest validation
# ---------------------------------------------------------------------------
if [[ -f "$BACKUP_DIR/manifest.json" ]]; then
  expected=$(grep -oE '"alembic_head"[[:space:]]*:[[:space:]]*"[^"]+"' "$BACKUP_DIR/manifest.json" \
    | sed -E 's/.*"([^"]+)"$/\1/')
  # Wait for backend to be up before asking it.
  for _ in $(seq 1 30); do
    if docker-compose -f docker-compose.yml exec -T backend curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  current=$(docker-compose -f docker-compose.yml exec -T backend alembic current 2>/dev/null \
    | tail -1 | awk '{print $1}' || echo "unknown")
  if [[ "$expected" == "$current" ]]; then
    ok "alembic head matches manifest ($current)"
  else
    warn "alembic head mismatch. expected=$expected current=$current"
    warn "Run: docker-compose -f docker-compose.yml exec backend alembic upgrade head"
  fi
fi

title "Restore complete"
