#!/usr/bin/env bash
# TrustedOSS Portal — point-in-time backup.
#
# Output: backups/YYYY-MM-DD-HHMMSS/
#   - postgres.sql.gz       (pg_dump --clean --if-exists | gzip)
#   - workspace.tar.gz      (tar.gz of the host workspace mount)
#   - manifest.json         (timestamp, alembic head, db size, image tags)
#
# Retention: backups older than ${BACKUP_RETENTION_DAYS:-7} days are removed
# at the end (skipped if --no-prune is passed).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

ok()    { printf "${GREEN}✓${RESET} %s\n" "$1"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }
title() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

PRUNE=1
for arg in "$@"; do
  case "$arg" in
    --no-prune) PRUNE=0 ;;
  esac
done

command -v docker-compose >/dev/null 2>&1 || fail "docker-compose (V1) is required."

# Source .env so we know WORKSPACE_HOST_PATH.
[[ -f .env ]] || fail ".env not found — run scripts/install.sh first."
# shellcheck disable=SC1091
set -a; . ./.env; set +a

WORKSPACE_HOST_PATH=${WORKSPACE_HOST_PATH:-/opt/trustedoss/workspace}

stamp=$(date +%Y-%m-%d-%H%M%S)
# Allow Celery / programmatic callers to pin the destination via BACKUP_DIR.
# When unset we fall back to the legacy behaviour (`backups/<stamp>`).
out_dir="${BACKUP_DIR:-backups/$stamp}"
mkdir -p "$out_dir"

title "Backup → $out_dir"

# ---------------------------------------------------------------------------
# 1. PostgreSQL dump
# ---------------------------------------------------------------------------
docker-compose -f docker-compose.yml exec -T postgres \
  pg_dump --clean --if-exists -U trustedoss trustedoss \
  | gzip > "$out_dir/postgres.sql.gz"
ok "wrote $out_dir/postgres.sql.gz ($(du -h "$out_dir/postgres.sql.gz" | cut -f1))"

# ---------------------------------------------------------------------------
# 2. Workspace tar
# ---------------------------------------------------------------------------
if [[ -d "$WORKSPACE_HOST_PATH" ]]; then
  tar -C "$(dirname "$WORKSPACE_HOST_PATH")" -czf "$out_dir/workspace.tar.gz" "$(basename "$WORKSPACE_HOST_PATH")"
  ok "wrote $out_dir/workspace.tar.gz ($(du -h "$out_dir/workspace.tar.gz" | cut -f1))"
else
  printf '%s\n' "  (workspace not present at $WORKSPACE_HOST_PATH — skipping)"
fi

# ---------------------------------------------------------------------------
# 3. Manifest
# ---------------------------------------------------------------------------
alembic_head=$(docker-compose -f docker-compose.yml exec -T backend alembic current 2>/dev/null \
  | tail -1 | awk '{print $1}' || echo "unknown")
db_size=$(docker-compose -f docker-compose.yml exec -T postgres \
  psql -U trustedoss -d trustedoss -tAc \
  "SELECT pg_size_pretty(pg_database_size('trustedoss'));" 2>/dev/null \
  | tr -d '[:space:]' || echo "unknown")

cat > "$out_dir/manifest.json" <<JSON
{
  "timestamp": "$stamp",
  "alembic_head": "$alembic_head",
  "db_size": "$db_size",
  "workspace_path": "$WORKSPACE_HOST_PATH"
}
JSON
ok "wrote $out_dir/manifest.json (alembic head = $alembic_head)"

# ---------------------------------------------------------------------------
# 4. Retention
# ---------------------------------------------------------------------------
if [[ $PRUNE -eq 1 ]]; then
  retention=${BACKUP_RETENTION_DAYS:-7}
  removed=$(find backups -mindepth 1 -maxdepth 1 -type d -mtime "+$retention" -print -exec rm -rf {} + | wc -l)
  if [[ $removed -gt 0 ]]; then
    ok "pruned $removed backup(s) older than $retention days"
  fi
fi

title "Backup complete"
printf "  %s\n" "$out_dir"
