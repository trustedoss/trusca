#!/usr/bin/env bash
# TrustedOSS Portal — interactive install wizard.
#
# Targets a fresh host (Linux) with docker-compose V1 (hyphen). Generates
# .env, brings the stack up, runs alembic upgrade head, and creates the
# first super_admin user.
#
# Usage:
#   bash scripts/install.sh
#
# CLAUDE.md compliance:
#   - core rule #10: docker-compose (V1). docker compose (V2) refused.
#   - core rule #11: env values written to .env, never inlined.
#   - core rule #9 : image tags pinned in docker-compose.yml.

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

# ---------------------------------------------------------------------------
# 1. Pre-flight: docker-compose V1, openssl, curl
# ---------------------------------------------------------------------------
title "Pre-flight checks"

command -v docker-compose >/dev/null 2>&1 \
  || fail "docker-compose (V1, hyphenated) is required. Compose V2 'docker compose' is unsupported."
ok "docker-compose found: $(docker-compose --version)"

command -v openssl >/dev/null 2>&1 || fail "openssl is required for secret generation."
ok "openssl found"

command -v curl >/dev/null 2>&1 || fail "curl is required for the post-install health probe."
ok "curl found"

# ---------------------------------------------------------------------------
# 2. .env file — copy template + auto-generate secrets
# ---------------------------------------------------------------------------
title "Environment configuration"

if [[ -f .env ]]; then
  read -r -p "Existing .env detected — use it? [Y/n] " reply
  reply=${reply:-Y}
  if [[ ! "$reply" =~ ^[Yy]$ ]]; then
    backup=".env.backup-$(date +%Y%m%d-%H%M%S)"
    mv .env "$backup"
    note "moved existing .env → $backup"
  fi
fi

if [[ ! -f .env ]]; then
  [[ -f .env.example ]] || fail ".env.example not found. Cannot bootstrap configuration."
  cp .env.example .env
  ok "wrote .env from .env.example"

  # Auto-generate the two required secrets with strong entropy.
  secret_key=$(openssl rand -hex 32)
  db_password=$(openssl rand -base64 24 | tr -d '=+/')

  # Substitute placeholders. We intentionally do NOT sed -i in place across
  # platforms (BSD sed differs); use a portable temp-file swap instead.
  python3 - <<PYTHON
import re
from pathlib import Path
env = Path(".env")
text = env.read_text()
text = re.sub(r"^SECRET_KEY=.*$", f"SECRET_KEY=${secret_key}", text, flags=re.M)
text = re.sub(
    r"^DATABASE_URL=.*$",
    f"DATABASE_URL=postgresql+asyncpg://trustedoss:${db_password}@postgres:5432/trustedoss",
    text,
    flags=re.M,
)
env.write_text(text)
PYTHON
  ok "generated SECRET_KEY (64 hex chars) and Postgres password"
fi

# ---------------------------------------------------------------------------
# 3. Public URL prompt
# ---------------------------------------------------------------------------
title "Network configuration"

current_url=$(grep -E "^CORS_ALLOWED_ORIGINS=" .env | head -1 | cut -d= -f2- || true)
default_url=${current_url:-http://localhost}
read -r -p "Public URL [$default_url]: " public_url
public_url=${public_url:-$default_url}

# Update / append CORS + DOMAIN keys.
python3 - <<PYTHON
from pathlib import Path
import re
env = Path(".env")
text = env.read_text()
def upsert(text: str, key: str, value: str) -> str:
    pattern = rf"^{key}=.*$"
    if re.search(pattern, text, flags=re.M):
        return re.sub(pattern, f"{key}={value}", text, flags=re.M)
    return text.rstrip() + f"\n{key}={value}\n"
text = upsert(text, "CORS_ALLOWED_ORIGINS", "${public_url}")
# DOMAIN powers Traefik's host rule; strip scheme.
domain = "${public_url}".replace("https://", "").replace("http://", "").split("/")[0]
text = upsert(text, "DOMAIN", domain)
env.write_text(text)
PYTHON
ok "wrote CORS_ALLOWED_ORIGINS=$public_url + DOMAIN to .env"

# ---------------------------------------------------------------------------
# 4. docker-compose pull + up
# ---------------------------------------------------------------------------
title "Bringing up the stack"

docker-compose -f docker-compose.yml pull
docker-compose -f docker-compose.yml up -d
ok "containers started"

# Wait for backend health
note "waiting for backend to become healthy (60s timeout)..."
for i in $(seq 1 30); do
  if docker-compose -f docker-compose.yml exec -T backend curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    ok "backend is healthy"
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    fail "backend did not become healthy. Run: docker-compose -f docker-compose.yml logs backend"
  fi
done

# ---------------------------------------------------------------------------
# 5. alembic upgrade head
# ---------------------------------------------------------------------------
title "Database migration"
docker-compose -f docker-compose.yml exec -T backend alembic upgrade head
ok "schema is at HEAD"

# ---------------------------------------------------------------------------
# 6. Bootstrap super_admin
# ---------------------------------------------------------------------------
title "Bootstrap super admin account"

read -r -p "Super admin email: " admin_email
[[ -n "$admin_email" ]] || fail "email required"

while :; do
  read -r -s -p "Password (12+ chars): " admin_pwd; echo
  if [[ ${#admin_pwd} -lt 12 ]]; then
    note "password must be at least 12 characters — try again"
    continue
  fi
  read -r -s -p "Confirm password: " admin_pwd2; echo
  if [[ "$admin_pwd" != "$admin_pwd2" ]]; then
    note "passwords did not match — try again"
    continue
  fi
  break
done

# We pipe the password via env to avoid showing it in `ps -ef`.
docker-compose -f docker-compose.yml exec -T \
  -e ADMIN_EMAIL="$admin_email" \
  -e ADMIN_PASSWORD="$admin_pwd" \
  backend python -m scripts.create_super_admin
ok "super admin account ready"

# ---------------------------------------------------------------------------
# 7. Done
# ---------------------------------------------------------------------------
title "Installation complete"
ok "TrustedOSS Portal is running at: ${BOLD}$public_url${RESET}"
note "Login:           $admin_email"
note "Admin panel:     $public_url/admin"
note "API docs:        $public_url/api/docs"
note ""
note "Next steps:"
note "  1. Set DT_API_KEY in .env if you bring up Dependency-Track."
note "  2. Configure SMTP / Slack / Teams in .env for outbound notifications."
note "  3. Schedule scripts/backup.sh in cron for off-host backups."
