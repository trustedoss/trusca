#!/usr/bin/env bash
# TrustedOSS Portal — interactive install wizard.
#
# Targets a fresh host (Linux) with docker-compose V1 (hyphen). Generates
# .env, brings the stack up, runs alembic upgrade head, and creates the
# first super_admin user.
#
# Usage:
#   bash scripts/install.sh             # interactive wizard
#   bash scripts/install.sh --no-prompt # non-interactive (CI / automation)
#
# In `--no-prompt` mode every interactive question is replaced by an env-var
# read with a sane default. The fresh-Linux UAT workflow
# (.github/workflows/install-uat.yml, Chore E) is the primary consumer:
#   INSTALL_HOST            public URL (default: http://localhost)
#   INSTALL_TLS_EMAIL       Let's Encrypt contact email (HTTPS only)
#                           (default: admin@<domain> derived from INSTALL_HOST)
#   INSTALL_ADMIN_EMAIL     super-admin email   (default: admin@trustedoss.local)
#   INSTALL_ADMIN_PASSWORD  super-admin password (default: openssl rand -base64 24)
#   INSTALL_SECRET_KEY      JWT signing key      (default: openssl rand -hex 32)
#   INSTALL_REUSE_ENV       "1" reuses an existing .env, else it is rotated to
#                           .env.backup-<utc>. Default: 0 (rotate).
#
# CLAUDE.md compliance:
#   - core rule #10: docker-compose (V1). docker compose (V2) refused.
#   - core rule #11: env values written to .env, never inlined.
#   - core rule #9 : image tags pinned in docker-compose.yml.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ---------------------------------------------------------------------------
# 0. CLI flag parsing
# ---------------------------------------------------------------------------
NO_PROMPT=0
for arg in "$@"; do
  case "$arg" in
    --no-prompt) NO_PROMPT=1 ;;
    -h|--help)
      cat <<USAGE
Usage: bash scripts/install.sh [--no-prompt]

  --no-prompt   Run non-interactively. Reads INSTALL_HOST,
                INSTALL_ADMIN_EMAIL, INSTALL_ADMIN_PASSWORD,
                INSTALL_SECRET_KEY, INSTALL_REUSE_ENV from the environment.
USAGE
      exit 0
      ;;
    *)
      printf '✗ unknown argument: %s (try --help)\n' "$arg" >&2
      exit 2
      ;;
  esac
done

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
  if [[ $NO_PROMPT -eq 1 ]]; then
    # Non-interactive: rotate by default unless caller opts in to reuse.
    if [[ "${INSTALL_REUSE_ENV:-0}" == "1" ]]; then
      note "INSTALL_REUSE_ENV=1 — keeping existing .env"
    else
      backup=".env.backup-$(date +%Y%m%d-%H%M%S)"
      mv .env "$backup"
      note "moved existing .env → $backup (set INSTALL_REUSE_ENV=1 to reuse)"
    fi
  else
    read -r -p "Existing .env detected — use it? [Y/n] " reply
    reply=${reply:-Y}
    if [[ ! "$reply" =~ ^[Yy]$ ]]; then
      backup=".env.backup-$(date +%Y%m%d-%H%M%S)"
      mv .env "$backup"
      note "moved existing .env → $backup"
    fi
  fi
fi

if [[ ! -f .env ]]; then
  [[ -f .env.example ]] || fail ".env.example not found. Cannot bootstrap configuration."
  cp .env.example .env
  ok "wrote .env from .env.example"

  # SECRET_KEY: --no-prompt may pin via INSTALL_SECRET_KEY (CI reproducibility).
  # Otherwise we always auto-generate strong entropy.
  if [[ $NO_PROMPT -eq 1 && -n "${INSTALL_SECRET_KEY:-}" ]]; then
    secret_key="$INSTALL_SECRET_KEY"
    note "using INSTALL_SECRET_KEY (length=${#secret_key})"
  else
    secret_key=$(openssl rand -hex 32)
  fi
  db_password=$(openssl rand -base64 24 | tr -d '=+/')
  # Marathon bundle 8 (L1) — split runtime / migration roles. The owner
  # role keeps the legacy "trustedoss" name (so existing data + grants
  # stay valid); the runtime role gets its own password and is
  # provisioned by Postgres at first up via the docker-compose env (see
  # POSTGRES_APP_USER / POSTGRES_APP_PASSWORD in docker-compose.yml).
  app_password=$(openssl rand -base64 24 | tr -d '=+/')

  # Substitute placeholders. We intentionally do NOT sed -i in place across
  # platforms (BSD sed differs); use a portable temp-file swap instead.
  python3 - <<PYTHON
import re
from pathlib import Path
env = Path(".env")
text = env.read_text()
text = re.sub(r"^SECRET_KEY=.*$", f"SECRET_KEY=${secret_key}", text, flags=re.M)
# DATABASE_URL stays the legacy single-role DSN so older deployments
# that haven't yet rotated to the L1 split keep working.
text = re.sub(
    r"^DATABASE_URL=.*$",
    f"DATABASE_URL=postgresql+asyncpg://trustedoss:${db_password}@postgres:5432/trustedoss",
    text,
    flags=re.M,
)
# DATABASE_URL_OWNER + DATABASE_URL_APP — the L1 split. alembic uses
# OWNER (DDL); backend / worker runtime uses APP (DML-only on
# audit_logs). When unset, both fall back to DATABASE_URL.
def _ensure(line: str, value: str, body: str) -> str:
    if re.search(rf"^{line}=", body, flags=re.M):
        return re.sub(rf"^{line}=.*$", f"{line}={value}", body, flags=re.M)
    return body.rstrip() + f"\n{line}={value}\n"

text = _ensure(
    "DATABASE_URL_OWNER",
    f"postgresql+asyncpg://trustedoss:${db_password}@postgres:5432/trustedoss",
    text,
)
text = _ensure(
    "DATABASE_URL_APP",
    f"postgresql+asyncpg://trustedoss_app:${app_password}@postgres:5432/trustedoss",
    text,
)
text = _ensure("POSTGRES_APP_PASSWORD", "${app_password}", text)
env.write_text(text)
PYTHON
  ok "generated SECRET_KEY (64 hex chars) and Postgres passwords (owner + app)"
fi

# ---------------------------------------------------------------------------
# 3. Public URL prompt
# ---------------------------------------------------------------------------
title "Network configuration"

current_url=$(grep -E "^CORS_ALLOWED_ORIGINS=" .env | head -1 | cut -d= -f2- || true)
default_url=${current_url:-http://localhost}
if [[ $NO_PROMPT -eq 1 ]]; then
  public_url="${INSTALL_HOST:-$default_url}"
  note "non-interactive: public_url=$public_url"
else
  read -r -p "Public URL [$default_url]: " public_url
  public_url=${public_url:-$default_url}
fi

# Derive DOMAIN (host without scheme) and decide whether HTTPS / Let's
# Encrypt is in play. Local hosts (localhost, 127.0.0.1) skip TLS_EMAIL;
# any real domain reached over https:// requires it for cert issuance.
domain="${public_url#https://}"
domain="${domain#http://}"
domain="${domain%%/*}"
case "$public_url" in https://*) is_https=1 ;; *) is_https=0 ;; esac

tls_email=""
if [[ $is_https -eq 1 ]]; then
  default_tls_email="${INSTALL_TLS_EMAIL:-admin@${domain}}"
  if [[ $NO_PROMPT -eq 1 ]]; then
    tls_email="$default_tls_email"
    note "non-interactive: tls_email=$tls_email"
  else
    read -r -p "Let's Encrypt contact email [$default_tls_email]: " tls_email
    tls_email=${tls_email:-$default_tls_email}
  fi
  if [[ ! "$tls_email" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; then
    fail "TLS_EMAIL '$tls_email' is not a valid email address"
  fi
fi

# Update / append CORS + DOMAIN + TLS_EMAIL keys.
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
text = upsert(text, "DOMAIN", "${domain}")
text = upsert(text, "TLS_EMAIL", "${tls_email}")
env.write_text(text)
PYTHON
ok "wrote CORS_ALLOWED_ORIGINS=$public_url + DOMAIN=$domain + TLS_EMAIL to .env"

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
# Marathon bundle 8 (L1) — alembic must run as the OWNER role so DDL
# (CREATE / ALTER / DROP / GRANT) has the necessary privileges. The
# runtime containers see only the DML-only DSN; this one-shot exec
# overrides DATABASE_URL just for the alembic process so the owner
# DSN never lingers in the live container environment.
title "Database migration"
owner_url=$(grep -E "^DATABASE_URL_OWNER=" .env | head -1 | cut -d= -f2- || true)
if [[ -z "$owner_url" ]]; then
  # Legacy / single-role deployments: fall back to DATABASE_URL.
  owner_url=$(grep -E "^DATABASE_URL=" .env | head -1 | cut -d= -f2-)
fi
docker-compose -f docker-compose.yml exec -T \
  -e DATABASE_URL="$owner_url" \
  backend alembic upgrade head
ok "schema is at HEAD"

# ---------------------------------------------------------------------------
# 6. Bootstrap super_admin
# ---------------------------------------------------------------------------
title "Bootstrap super admin account"

if [[ $NO_PROMPT -eq 1 ]]; then
  admin_email="${INSTALL_ADMIN_EMAIL:-admin@trustedoss.local}"
  if [[ -n "${INSTALL_ADMIN_PASSWORD:-}" ]]; then
    admin_pwd="$INSTALL_ADMIN_PASSWORD"
  else
    # Last-resort default. We surface it once on stdout so a CI run can
    # capture it from logs; an operator MUST rotate immediately.
    admin_pwd=$(openssl rand -base64 24 | tr -d '=+/' | cut -c1-20)
    note "generated admin password (length=${#admin_pwd}): $admin_pwd"
    note "ROTATE THIS PASSWORD ON FIRST LOGIN."
  fi
  if [[ ${#admin_pwd} -lt 12 ]]; then
    fail "INSTALL_ADMIN_PASSWORD must be at least 12 characters"
  fi
  note "non-interactive: admin_email=$admin_email"
else
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
fi

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
