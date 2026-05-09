#!/usr/bin/env bash
# TrustedOSS Portal — upgrade the running stack to a newer image set.
#
# Flow:
#   1. Take a pre-upgrade backup (always — safety net).
#   2. Pull the new images defined in docker-compose.yml.
#   3. up -d  — Compose recreates only services whose image hash changed.
#   4. Run alembic upgrade head.
#   5. Wait for /health to return 200.
#
# CLAUDE.md compliance:
#   - core rule #6 : Alembic forward-only. Rollback path = restore.sh.
#   - core rule #10: docker-compose (V1, hyphenated).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

ok()    { printf "${GREEN}✓${RESET} %s\n" "$1"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }
note()  { printf "  %s\n" "$1"; }
title() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

command -v docker-compose >/dev/null 2>&1 || fail "docker-compose (V1) is required."

# ---------------------------------------------------------------------------
# 1. Pre-upgrade backup
# ---------------------------------------------------------------------------
title "Pre-upgrade backup"
note "Running scripts/backup.sh — this is mandatory before pulling new images."
bash "$ROOT_DIR/scripts/backup.sh"
ok "backup complete"

# ---------------------------------------------------------------------------
# 2. Pull new images
# ---------------------------------------------------------------------------
title "Pulling new images"
docker-compose -f docker-compose.yml pull
ok "images pulled"

# ---------------------------------------------------------------------------
# 3. Recreate containers
# ---------------------------------------------------------------------------
title "Recreating containers"
note "The portal will be briefly unavailable (typically <30s)."
docker-compose -f docker-compose.yml up -d
ok "containers running"

# ---------------------------------------------------------------------------
# 4. alembic upgrade head
# ---------------------------------------------------------------------------
title "Database migration"
docker-compose -f docker-compose.yml exec -T backend alembic upgrade head
ok "schema is at HEAD"

# ---------------------------------------------------------------------------
# 5. Health probe
# ---------------------------------------------------------------------------
title "Post-upgrade health probe"
for _ in $(seq 1 30); do
  if docker-compose -f docker-compose.yml exec -T backend curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    ok "backend is healthy"
    title "Upgrade complete"
    note "If something looks off, restore the pre-upgrade backup:"
    note "  bash scripts/restore.sh \$(ls -td backups/* | head -1)"
    exit 0
  fi
  sleep 2
done
fail "backend did not become healthy. Inspect: docker-compose -f docker-compose.yml logs backend"
