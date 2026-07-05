#!/usr/bin/env bash
# TRUSCA — interactive install wizard.
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
#   - core rule #10: our DEV/CI environment is docker-compose V1 (hyphen). This
#     install script ships to END USERS whose hosts increasingly carry only the
#     Compose V2 plugin (`docker compose`, V1 reached EOL in 2023). To keep the
#     low-friction install path working for them we prefer V1 and fall back to
#     V2 (see the $DC selection below). This does NOT change the project's
#     internal V1-only stance — CLAUDE.md rule #10 and our CI stay V1.
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
# 1. Pre-flight: Docker Compose (V1 preferred, V2 fallback), openssl, curl
# ---------------------------------------------------------------------------
title "Pre-flight checks"

# $DC is the Compose invocation used everywhere below. We prefer V1
# (`docker-compose`, the project standard per CLAUDE.md rule #10) and fall
# back to the V2 plugin (`docker compose`) when V1 is absent — this is a
# DEPLOY-TARGET compatibility shim for end users on modern hosts, not a change
# to the project's V1-only dev/CI stance. Fail only when neither is present.
if command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
  ok "docker-compose (V1) found: $(docker-compose --version)"
elif docker compose version >/dev/null 2>&1; then
  DC="docker compose"
  ok "docker compose (V2 plugin) found: $(docker compose version | head -1)"
  note "V1 preferred (project standard) but unavailable — using V2 fallback for this host."
else
  fail "Docker Compose is required: install docker-compose (V1) or the 'docker compose' (V2) plugin."
fi

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
else
  # Reusing an operator-owned .env — sync new keys from the example without
  # ever touching what the operator already set. See scripts/lib/env_sync.sh
  # for the append-only contract.
  # shellcheck source=scripts/lib/env_sync.sh
  source "$ROOT_DIR/scripts/lib/env_sync.sh"
  env_append_only_sync .env.example .env
fi

# ---------------------------------------------------------------------------
# 2b. Secrets — idempotent (append-only), owner/app DSN consistency (L1)
# ---------------------------------------------------------------------------
# Runs for BOTH the fresh (.env just copied) and reuse paths. A GENUINE secret
# is PRESERVED across re-runs — rotating it would (a) break auth against an
# already-initialised Postgres volume (the superuser password is fixed at the
# volume's first boot and never changes) and (b) violate env_sync's append-only
# contract. We only ever FILL IN a MISSING or PLACEHOLDER secret. A fresh
# install therefore gets a STRONG RANDOM owner password (the .env.example
# default is a placeholder, not a real value — see PLACEHOLDER_PASSWORDS), while
# a re-run keeps whatever the first run generated (idempotent).
#
# Consistency invariants enforced on every run (this is bug #1's fix):
#   * owner DSN password  == POSTGRES_PASSWORD.  The owner role IS the Postgres
#     superuser (POSTGRES_USER), and Postgres initialises its password from
#     POSTGRES_PASSWORD on first boot. DATABASE_URL / DATABASE_URL_OWNER must
#     therefore carry that SAME password or the owner alembic pass (Step 5)
#     fails password auth on a fresh volume.
#   * app DSN password    == POSTGRES_APP_PASSWORD  (only when L1 is enabled).
#
# Security (L1 boundary): the owner role is the SUPERUSER, so a fresh install
# must never ship it with a well-known password — otherwise any in-network
# foothold could connect as superuser and run DDL (DROP the audit trigger, etc.),
# bypassing the whole runtime/owner split. Placeholder owner passwords are
# regenerated; only a real operator-chosen value survives.
#
# L1 (Marathon bundle 8) role split is enabled when POSTGRES_APP_PASSWORD is
# non-empty — either already in .env (reuse) or exported in the environment
# (fresh / CI). An empty app password keeps the stack single-role (owner ==
# runtime), which is the default fresh install. Operator-provided passwords with
# docker-compose-hostile characters ('$', leading/trailing whitespace) should be
# avoided — the generated path uses a compose-safe alphabet to sidestep this.
#
# SECRET_KEY: --no-prompt may pin via INSTALL_SECRET_KEY (CI reproducibility);
# otherwise we preserve an existing non-placeholder key and only generate one
# when the file still carries the .env.example placeholder.
if [[ $NO_PROMPT -eq 1 && -n "${INSTALL_SECRET_KEY:-}" ]]; then
  pinned_secret_key="$INSTALL_SECRET_KEY"
  note "using INSTALL_SECRET_KEY (length=${#pinned_secret_key})"
else
  pinned_secret_key=""
fi
# Generated secrets use a COMPOSE-SAFE alphabet — openssl base64 minus '=+/'
# leaves [A-Za-z0-9] only. That guarantees a generated value never needs
# URL-encoding in a DSN and never trips docker-compose .env interpolation
# ('$', '#', whitespace), so the raw POSTGRES_PASSWORD line and the DSN stay
# byte-consistent (closes the special-char raw/DSN mismatch class).
gen_secret_key=$(openssl rand -hex 32)
gen_db_password=$(openssl rand -base64 24 | tr -d '=+/')

# Environment overrides (optional). When exported these seed .env so an
# operator / CI can enable L1 or pin passwords WITHOUT hand-editing .env,
# honouring rule #11 (values live in .env, not inlined into commands).
env_pg_password="${POSTGRES_PASSWORD:-}"
env_pg_user="${POSTGRES_USER:-}"
env_pg_db="${POSTGRES_DB:-}"
env_app_password="${POSTGRES_APP_PASSWORD:-}"
env_app_user="${POSTGRES_APP_USER:-}"

# Secrets are handed to python via the ENVIRONMENT, never argv: /proc/<pid>/cmdline
# is world-readable (a local unprivileged user could scrape a pinned SECRET_KEY /
# DB password from argv), whereas /proc/<pid>/environ is 0400 owner-only. The
# heredoc stays quoted (<<'PYTHON') so the shell does not expand the body.
mode=$(
  PINNED_SECRET="$pinned_secret_key" \
  GEN_SECRET="$gen_secret_key" \
  GEN_DB_PASSWORD="$gen_db_password" \
  ENV_PG_USER="$env_pg_user" \
  ENV_PG_PASSWORD="$env_pg_password" \
  ENV_PG_DB="$env_pg_db" \
  ENV_APP_USER="$env_app_user" \
  ENV_APP_PASSWORD="$env_app_password" \
  python3 - <<'PYTHON'
import os, re
from pathlib import Path
from urllib.parse import quote

pinned_secret    = os.environ["PINNED_SECRET"]
gen_secret       = os.environ["GEN_SECRET"]
gen_db_password  = os.environ["GEN_DB_PASSWORD"]
env_pg_user      = os.environ["ENV_PG_USER"]
env_pg_password  = os.environ["ENV_PG_PASSWORD"]
env_pg_db        = os.environ["ENV_PG_DB"]
env_app_user     = os.environ["ENV_APP_USER"]
env_app_password = os.environ["ENV_APP_PASSWORD"]

PLACEHOLDER_SECRET = "change-this-to-a-random-secret-key-min-32-chars"
# Well-known non-secret owner-password defaults that must NEVER survive as a
# real Postgres SUPERUSER password (they are public in .env.example / dev
# compose). Treated as "regenerate me", exactly like the SECRET_KEY placeholder,
# so a fresh install never ships the owner role with a known password — which
# would let any in-network foothold connect as superuser and defeat the L1
# owner/app split. A genuine operator value (incl. env override) is preserved.
PLACEHOLDER_PASSWORDS = {"", "trustedoss", "changeme", "change_me", "CHANGE_ME"}

env = Path(".env")
text = env.read_text()

def get(key, default=""):
    m = re.search(rf"^{re.escape(key)}=(.*)$", text, flags=re.M)
    return m.group(1) if m else default

def upsert(body, key, value):
    pattern = rf"^{re.escape(key)}=.*$"
    # Replace with a CALLABLE so `value` is treated as a literal — a backslash /
    # \g<0> / \1 inside a pinned secret must NOT be interpreted as an re
    # backreference (that would silently corrupt the written secret).
    if re.search(pattern, body, flags=re.M):
        return re.sub(pattern, lambda _m: f"{key}={value}", body, flags=re.M)
    return body.rstrip() + f"\n{key}={value}\n"

# --- SECRET_KEY: pinned > existing-non-placeholder > generated -------------
cur_secret = get("SECRET_KEY")
if pinned_secret:
    secret = pinned_secret
elif cur_secret and cur_secret != PLACEHOLDER_SECRET:
    secret = cur_secret            # preserve — idempotent re-run
else:
    secret = gen_secret
text = upsert(text, "SECRET_KEY", secret)

# --- Owner (superuser) identity + password --------------------------------
# Owner password precedence: explicit env override > a GENUINE existing value
# (preserved -> idempotent, and matches an already-initialised volume) > a
# STRONG GENERATED value. A placeholder / public default (see the set above) is
# treated as "generate", so a fresh install always gets a random superuser
# password consistent with the owner DSN.
pg_user = env_pg_user or get("POSTGRES_USER") or "trustedoss"
pg_db = env_pg_db or get("POSTGRES_DB") or "trustedoss"
cur_pg = get("POSTGRES_PASSWORD")
pg_password = (
    env_pg_password
    or (cur_pg if cur_pg not in PLACEHOLDER_PASSWORDS else "")
    or gen_db_password
)
text = upsert(text, "POSTGRES_USER", pg_user)
text = upsert(text, "POSTGRES_DB", pg_db)
text = upsert(text, "POSTGRES_PASSWORD", pg_password)

owner_dsn = (
    f"postgresql+asyncpg://{quote(pg_user, safe='')}:"
    f"{quote(pg_password, safe='')}@postgres:5432/{pg_db}"
)
# DATABASE_URL is the runtime DSN for a SINGLE-role stack and the owner DSN
# baseline the compose file falls back to; DATABASE_URL_OWNER is the DDL DSN
# alembic runs as. Both must match POSTGRES_PASSWORD.
text = upsert(text, "DATABASE_URL", owner_dsn)
text = upsert(text, "DATABASE_URL_OWNER", owner_dsn)

# --- App role (L1): enabled iff POSTGRES_APP_PASSWORD is non-empty ---------
app_user = env_app_user or get("POSTGRES_APP_USER") or "trustedoss_app"
app_password = env_app_password or get("POSTGRES_APP_PASSWORD")
if app_password:
    app_dsn = (
        f"postgresql+asyncpg://{quote(app_user, safe='')}:"
        f"{quote(app_password, safe='')}@postgres:5432/{pg_db}"
    )
    text = upsert(text, "POSTGRES_APP_USER", app_user)
    text = upsert(text, "POSTGRES_APP_PASSWORD", app_password)
    text = upsert(text, "DATABASE_URL_APP", app_dsn)

env.write_text(text)
print("L1" if app_password else "single-role")
PYTHON
)
ok "secrets synced (idempotent) — strong owner password, DSN pinned to POSTGRES_PASSWORD [${mode}]"

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
# 3b. AUTO_MIGRATE policy — disable on L1 role-separated stacks (H1)
# ---------------------------------------------------------------------------
# The backend container's entrypoint auto-applies `alembic upgrade head` on
# start when AUTO_MIGRATE=true. Under an L1 role-separated stack the runtime
# container holds only the DML-only app DSN (DATABASE_URL_APP), so an
# entrypoint DDL run would FAIL — install.sh runs the authoritative migration
# as the OWNER role in Step 5 instead. Detect L1 (DATABASE_URL_OWNER is set
# AND differs from the runtime DSN) and pin AUTO_MIGRATE=false so the
# entrypoint does not even attempt a doomed app-role DDL. Single-role stacks
# (no split, or owner == runtime) keep the default true.
title "Migration policy (AUTO_MIGRATE)"
owner_dsn=$(grep -E "^DATABASE_URL_OWNER=" .env | head -1 | cut -d= -f2- || true)
app_dsn=$(grep -E "^DATABASE_URL_APP=" .env | head -1 | cut -d= -f2- || true)
runtime_dsn=$(grep -E "^DATABASE_URL=" .env | head -1 | cut -d= -f2- || true)
# The runtime container's effective DSN is DATABASE_URL_APP when set, else
# DATABASE_URL (mirrors docker-compose.yml's `${DATABASE_URL_APP:-${DATABASE_URL}}`).
effective_runtime_dsn="${app_dsn:-$runtime_dsn}"
is_l1=0
if [[ -n "$owner_dsn" && -n "$effective_runtime_dsn" && "$owner_dsn" != "$effective_runtime_dsn" ]]; then
  is_l1=1
fi

python3 - "$is_l1" <<'PYTHON'
import re, sys
from pathlib import Path
is_l1 = sys.argv[1] == "1"
env = Path(".env")
text = env.read_text()
def upsert(text: str, key: str, value: str) -> str:
    pattern = rf"^{key}=.*$"
    if re.search(pattern, text, flags=re.M):
        return re.sub(pattern, f"{key}={value}", text, flags=re.M)
    return text.rstrip() + f"\n{key}={value}\n"
if is_l1:
    text = upsert(text, "AUTO_MIGRATE", "false")
    env.write_text(text)
PYTHON

if [[ $is_l1 -eq 1 ]]; then
  ok "detected L1 role-separated stack (DATABASE_URL_OWNER differs from runtime DSN)"
  note "set AUTO_MIGRATE=false in .env — the runtime app role cannot run DDL;"
  note "this script applies migrations as the owner role in Step 5 instead."
else
  note "single-role stack — AUTO_MIGRATE left at default (true); the backend"
  note "container migrates on start, Step 5 below is then an idempotent re-check."
fi

# ---------------------------------------------------------------------------
# 4. Staged bring-up — schema BEFORE the runtime fleet (bug #2 fix)
# ---------------------------------------------------------------------------
# A single `$DC up -d` (whole stack) DEADLOCKS on an L1 role-separated stack:
# worker / beat declare `depends_on backend: service_healthy`, and backend's
# healthcheck probes /health/ready (200 only when the schema == Alembic HEAD).
# Under L1 AUTO_MIGRATE=false, so the entrypoint SKIPS migration and
# /health/ready stays 503 until this script applies the schema as the OWNER
# role — but that owner pass (old Step 5) came AFTER `up -d`, which had already
# failed waiting for a backend that could never go healthy without a schema.
#
# Fix — stage the boot so the schema exists before the fleet that gates on it:
#   Stage 1  postgres + redis + backend      (backend answers liveness /health)
#   Stage 2  owner-role `alembic upgrade head` (authoritative DDL; idempotent)
#   Stage 3  up -d (whole stack)             (backend now /health/ready -> healthy
#                                             -> worker / beat / frontend start)
# On a single-role stack (AUTO_MIGRATE=true) the order is equally safe: the
# entrypoint migrates on start, Stage 2 is an idempotent no-op, Stage 3 is a
# normal full up. This mirrors the proven staged sequence in
# .github/workflows/install-uat.yml (install-uat-l1 job).
title "Bringing up the stack (staged: schema before the runtime fleet)"

# shellcheck disable=SC2086  # $DC may be "docker compose" (two words) — intentional word-split.
$DC -f docker-compose.yml pull

# Stage 1 — backend + its data deps ONLY. worker / beat / frontend are held
# back until the schema is in place (Stage 3).
# shellcheck disable=SC2086
$DC -f docker-compose.yml up -d postgres redis backend
ok "postgres + redis + backend started"

# Wait for backend LIVENESS. Probe /health (NOT /health/ready): under L1 the
# schema is not applied yet, so /health/ready is 503 until Stage 2 — but
# /health answers as soon as uvicorn binds, without touching the DB.
note "waiting for backend liveness (/health, 60s timeout)..."
for i in $(seq 1 30); do
  # shellcheck disable=SC2086
  if $DC -f docker-compose.yml exec -T backend curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    ok "backend is live"
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    fail "backend did not answer /health. Run: $DC -f docker-compose.yml logs backend"
  fi
done

# ---------------------------------------------------------------------------
# 5. alembic upgrade head (owner role — authoritative DDL pass) — Stage 2
# ---------------------------------------------------------------------------
# Marathon bundle 8 (L1) — alembic must run as the OWNER role so DDL
# (CREATE / ALTER / DROP / GRANT) has the necessary privileges. Under L1 the
# runtime container holds only the DML-only app DSN, so the entrypoint's
# auto-migration would FAIL on DDL; this one-shot exec overrides DATABASE_URL
# with the owner DSN JUST for the alembic process — the owner DSN never lingers
# in the live container environment (the L1 security contract). It is
# idempotent (already-applied revisions are skipped), so on a single-role stack
# (where the entrypoint already migrated) this is a harmless no-op confirmation.
title "Database migration (owner role)"
owner_url=$(grep -E "^DATABASE_URL_OWNER=" .env | head -1 | cut -d= -f2- || true)
if [[ -z "$owner_url" ]]; then
  # Legacy / single-role deployments: fall back to DATABASE_URL.
  owner_url=$(grep -E "^DATABASE_URL=" .env | head -1 | cut -d= -f2-)
fi
# shellcheck disable=SC2086
$DC -f docker-compose.yml exec -T \
  -e DATABASE_URL="$owner_url" \
  backend alembic upgrade head
ok "schema is at HEAD"

# Wait for READINESS before starting the fleet. /health/ready flips to 200 once
# the schema == HEAD (read by the runtime role), which is what turns backend
# `healthy` so Stage 3's `depends_on backend: service_healthy` proceeds without
# blocking. On single-role this is already 200 (entrypoint migrated on start).
note "waiting for backend readiness (/health/ready, 60s timeout)..."
for i in $(seq 1 30); do
  # shellcheck disable=SC2086
  if $DC -f docker-compose.yml exec -T backend curl -fsS http://localhost:8000/health/ready >/dev/null 2>&1; then
    ok "backend is ready (schema visible to the runtime role)"
    break
  fi
  sleep 2
  if [[ $i -eq 30 ]]; then
    fail "backend /health/ready never turned 200. Run: $DC -f docker-compose.yml logs backend"
  fi
done

# Stage 3 — schema is at HEAD and backend is ready, so it can now go healthy.
# Bring up the whole stack; worker / beat / frontend (+ traefik) start once
# their `depends_on backend: service_healthy` gate is satisfied.
title "Starting the runtime fleet (worker, beat, frontend)"
# shellcheck disable=SC2086
$DC -f docker-compose.yml up -d
ok "all containers started"

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
# shellcheck disable=SC2086
$DC -f docker-compose.yml exec -T \
  -e ADMIN_EMAIL="$admin_email" \
  -e ADMIN_PASSWORD="$admin_pwd" \
  backend python -m scripts.create_super_admin
ok "super admin account ready"

# ---------------------------------------------------------------------------
# 7. Done
# ---------------------------------------------------------------------------
title "Installation complete"
ok "TRUSCA is running at: ${BOLD}$public_url${RESET}"
note "Login:           $admin_email"
note "Admin panel:     $public_url/admin"
note "API docs:        $public_url/api/docs"
note ""
note "Next steps:"
note "  1. Vulnerability data: the worker downloads the Trivy DB (~600 MB) on"
note "     first boot — findings populate within 1-3 minutes. For air-gapped"
note "     sites set TRIVY_DB_REPOSITORY in .env to point at an internal mirror."
note "     Status visible at /admin/health once W6-#43e (Trivy DB panel) lands."
note "  2. Configure SMTP / Slack / Teams in .env for outbound notifications."
note "  3. Schedule scripts/backup.sh in cron for off-host backups."
