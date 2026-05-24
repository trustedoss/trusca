#!/usr/bin/env bash
# TrustedOSS Portal — one-command EVALUATION bring-up (v2.1 Track B / B2).
#
# Stands the portal up on a SMALL host (2 vCPU / 4 GB RAM) and seeds a
# realistic demo dataset, so an evaluator goes from `git clone` to a populated
# dashboard in one command — no Dependency-Track, no manual migration, no empty
# screen.
#
#   ./scripts/eval-up.sh              # interactive-friendly (still fully auto)
#   ./scripts/eval-up.sh --no-prompt  # CI / automation (no TTY questions)
#
# What it does:
#   1. Pick the Compose binary (V1 `docker-compose` preferred per CLAUDE.md
#      rule #10; falls back to the `docker compose` V2 plugin for end-user
#      hosts only).
#   2. Ensure a usable .env (copy from .env.example, generate a SECRET_KEY,
#      pin eval-friendly localhost CORS). Never overwrites an existing .env.
#   3. Bring up the EVAL stack:
#        docker-compose -f docker-compose.yml -f docker-compose.eval.yml up -d
#      with --compatibility so the eval resource limits actually bind on a
#      non-Swarm host.
#   4. Wait for the backend READINESS gate — GET /health/ready (B1): 200 means
#      the Postgres schema is at the Alembic HEAD (AUTO_MIGRATE=true applies it
#      on start). If AUTO_MIGRATE is off, run `alembic upgrade head` as a
#      fallback, then re-poll.
#   5. Seed the idempotent demo dataset (scripts/seed_demo.py, APP_ENV=demo).
#   6. Print the URL + demo accounts / passwords.
#
# This is SEPARATE from install.sh (which is the prod wizard: TLS, owner-role
# migration, interactive super-admin). eval-up.sh shares NO state with it
# beyond the .env file it bootstraps; running install.sh afterwards on the same
# host still works.
#
# DT-less: the eval overlay does not run Dependency-Track. The portal serves
# the seeded / cached vulnerability data via the DT circuit breaker (OPEN), so
# the dashboard is populated without a 4 GB DT heap. See
# docs-site .../installation/docker-compose.md "Evaluation" section.
#
# CLAUDE.md compliance: bash 4+, `set -euo pipefail`, shellcheck-clean,
# docker-compose V1 preferred, image tags pinned in the compose files,
# env read at runtime (never baked).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_FILE="docker-compose.yml"
EVAL_FILE="docker-compose.eval.yml"

# ---------------------------------------------------------------------------
# 0. CLI flags
# ---------------------------------------------------------------------------
NO_PROMPT=0
for arg in "$@"; do
  case "$arg" in
    --no-prompt) NO_PROMPT=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: bash scripts/eval-up.sh [--no-prompt]

  Stand up the lightweight EVALUATION stack (2 vCPU / 4 GB target) and seed a
  demo dataset. DT-less; the portal serves seeded/cached findings.

  --no-prompt   Run non-interactively (CI). Reads optional overrides:
                  EVAL_URL     evaluator-facing URL (default http://localhost)
                  CELERY_CONCURRENCY  worker slots (default 1)
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
YELLOW='\033[0;33m'
BOLD='\033[1m'
RESET='\033[0m'

ok()    { printf "${GREEN}✓${RESET} %s\n" "$1"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$1" >&2; }
fail()  { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }
note()  { printf "  %s\n" "$1"; }
title() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

# ---------------------------------------------------------------------------
# 1. Pre-flight — Compose binary (V1 preferred), openssl
# ---------------------------------------------------------------------------
title "Pre-flight checks"

# $DC is an ARRAY so a two-word "docker compose" V2 fallback word-splits safely
# without the SC2086 dance. V1 (docker-compose, hyphen) is the project standard
# (CLAUDE.md rule #10); the V2 fallback is a deploy-target shim for end users.
if command -v docker-compose >/dev/null 2>&1; then
  DC=(docker-compose)
  ok "docker-compose (V1) found: $(docker-compose --version 2>&1 | head -1)"
elif docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
  ok "docker compose (V2 plugin) found: $(docker compose version 2>&1 | head -1)"
  note "V1 preferred (project standard) but unavailable — using V2 fallback."
else
  fail "Docker Compose is required: install docker-compose (V1) or the 'docker compose' (V2) plugin."
fi

command -v openssl >/dev/null 2>&1 || fail "openssl is required for SECRET_KEY generation."
ok "openssl found"

[[ -f "$BASE_FILE" ]] || fail "$BASE_FILE not found (run from the repo root)."
[[ -f "$EVAL_FILE" ]] || fail "$EVAL_FILE not found (run from the repo root)."
ok "compose files present: $BASE_FILE + $EVAL_FILE"

# ---------------------------------------------------------------------------
# 2. .env bootstrap — never clobber an existing one
# ---------------------------------------------------------------------------
title "Environment configuration"

EVAL_URL="${EVAL_URL:-http://localhost}"

if [[ -f .env ]]; then
  ok "using existing .env (left untouched)"
else
  [[ -f .env.example ]] || fail ".env.example not found; cannot bootstrap .env."
  cp .env.example .env
  secret_key="$(openssl rand -hex 32)"
  # Portable in-place edit (BSD vs GNU sed differ) — do it in Python like
  # install.sh. Eval defaults: a strong SECRET_KEY + localhost CORS so the SPA
  # talks to the API over plain HTTP, and APP_ENV=demo so seed_demo.py is
  # allowed to run.
  python3 - "$secret_key" "$EVAL_URL" <<'PYEOF'
import re, sys
from pathlib import Path
secret_key, eval_url = sys.argv[1], sys.argv[2]
env = Path(".env")
text = env.read_text()
def upsert(text, key, value):
    pat = rf"^{key}=.*$"
    if re.search(pat, text, flags=re.M):
        return re.sub(pat, f"{key}={value}", text, flags=re.M)
    return text.rstrip() + f"\n{key}={value}\n"
text = upsert(text, "SECRET_KEY", secret_key)
text = upsert(text, "APP_ENV", "demo")
# Eval routes the SPA + API over the same origin via Traefik on plain HTTP, so
# the evaluator URL is the only allowed origin.
text = upsert(text, "CORS_ALLOWED_ORIGINS", eval_url)
env.write_text(text)
PYEOF
  ok "wrote .env from .env.example (SECRET_KEY generated, APP_ENV=demo, CORS=$EVAL_URL)"
fi

# APP_ENV must be dev or demo for seed_demo.py to run. The eval overlay defaults
# APP_ENV to demo, but if the operator reused a prod .env we surface it loudly.
app_env="$(grep -E '^APP_ENV=' .env | head -1 | cut -d= -f2- || true)"
app_env="${app_env:-demo}"
case "$app_env" in
  dev|demo) ok "APP_ENV=$app_env — demo seed allowed" ;;
  *)
    warn "APP_ENV=$app_env in .env — seed_demo.py only runs under dev/demo."
    warn "This script will export APP_ENV=demo to the stack so the seed succeeds."
    app_env="demo"
    ;;
esac

# ---------------------------------------------------------------------------
# 3. Bring up the eval stack
# ---------------------------------------------------------------------------
title "Bringing up the evaluation stack (2 vCPU / 4 GB target, DT-less)"

# In interactive mode, give the operator one chance to bail before we pull
# (potentially large) images. --no-prompt skips this for CI / automation.
if [[ $NO_PROMPT -eq 0 ]]; then
  read -r -p "Pull images and start the eval stack now? [Y/n] " reply
  reply=${reply:-Y}
  [[ "$reply" =~ ^[Yy]$ ]] || fail "aborted by operator (no changes made beyond .env)."
fi

# --compatibility makes the eval deploy.resources.limits bind on a non-Swarm
# host. APP_ENV is exported so the overlay's ${APP_ENV:-demo} resolves to demo
# even if .env says prod (handled above).
note "pulling pinned images (ghcr.io/trustedoss/*:\${IMAGE_TAG}) ..."
APP_ENV="$app_env" "${DC[@]}" --compatibility -f "$BASE_FILE" -f "$EVAL_FILE" pull
note "starting containers ..."
APP_ENV="$app_env" "${DC[@]}" --compatibility -f "$BASE_FILE" -f "$EVAL_FILE" up -d
ok "containers started"

dc() { APP_ENV="$app_env" "${DC[@]}" -f "$BASE_FILE" -f "$EVAL_FILE" "$@"; }

# ---------------------------------------------------------------------------
# 4. Readiness gate — GET /health/ready (B1): 200 == schema at Alembic HEAD
# ---------------------------------------------------------------------------
title "Waiting for backend readiness (schema at Alembic HEAD)"

ready=0
# 60 probes x 5s = up to 5 minutes — generous for a cold image + first migrate.
for i in $(seq 1 60); do
  if dc exec -T backend curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    ready=1
    ok "backend is READY (/health/ready = 200) after ${i} probe(s)"
    break
  fi
  sleep 5
  [[ $((i % 6)) -eq 0 ]] && note "still waiting for /health/ready ... (${i}/60)"
done

if [[ $ready -ne 1 ]]; then
  # Readiness never flipped to 200 — most likely AUTO_MIGRATE=false so the schema
  # was never applied. Try a one-shot migration (single-role eval: the runtime
  # DSN owns the schema), then re-poll briefly.
  warn "/health/ready did not reach 200 in time — attempting a one-shot migration."
  if dc exec -T backend alembic upgrade head; then
    ok "alembic upgrade head applied; re-checking readiness"
    for i in $(seq 1 12); do
      if dc exec -T backend curl -fsS http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
        ready=1
        ok "backend is READY after the fallback migration"
        break
      fi
      sleep 5
    done
  else
    warn "fallback 'alembic upgrade head' failed — see: ${DC[*]} -f $BASE_FILE -f $EVAL_FILE logs backend"
  fi
fi

[[ $ready -eq 1 ]] || fail "backend never became ready. Inspect: ${DC[*]} -f $BASE_FILE -f $EVAL_FILE logs backend"

# ---------------------------------------------------------------------------
# 5. Seed the demo dataset (idempotent; APP_ENV demo/dev gated)
# ---------------------------------------------------------------------------
title "Seeding the demo dataset (idempotent)"

# seed_demo.py prints a JSON line on stdout. It is idempotent — re-running this
# script is safe. The generated demo super-admin password (when not pinned via
# DEMO_SUPER_ADMIN_PASSWORD) is emitted as a JSON event we surface below.
seed_out="$(dc exec -T -e APP_ENV="$app_env" backend python scripts/seed_demo.py 2>&1 || true)"
if printf '%s\n' "$seed_out" | grep -q '"ok": true'; then
  ok "demo dataset seeded (or already present)"
else
  warn "seed_demo.py did not report ok — output below:"
  printf '%s\n' "$seed_out" >&2
fi

# Extract the generated super-admin password if seed_demo emitted one this run.
gen_pw="$(printf '%s\n' "$seed_out" \
  | python3 -c 'import sys,json
pw=""
for line in sys.stdin:
    line=line.strip()
    if not line.startswith("{"):
        continue
    try:
        obj=json.loads(line)
    except Exception:
        continue
    if obj.get("event")=="seed_demo.generated_password":
        pw=obj.get("password","")
print(pw)' 2>/dev/null || true)"

# ---------------------------------------------------------------------------
# 6. Done — print the URL + demo accounts
# ---------------------------------------------------------------------------
title "Evaluation stack is up"
ok "TrustedOSS Portal (evaluation): ${BOLD}${EVAL_URL}${RESET}"
note ""
note "Demo accounts (created by scripts/seed_demo.py):"
note "  super admin : admin@demo.trustedoss.dev"
note "  team admins : frontend-admin@demo.trustedoss.dev / backend-admin@... / security-admin@..."
note "  developer   : dev@demo.trustedoss.dev"
if [[ -n "$gen_pw" ]]; then
  note "  password    : $gen_pw   (generated this run — store it; not persisted)"
else
  note "  password    : the value of DEMO_SUPER_ADMIN_PASSWORD in your .env"
  note "                (set it before first run to pin a known password)."
fi
note ""
note "Notes:"
note "  • DT-less eval: vulnerability data is the seeded cache (DT breaker OPEN)."
note "  • Real source scans need the full prod worker (6 GB) — eval is sized for"
note "    browsing the seeded dataset, not for production scanning."
note "  • Tear down:  ${DC[*]} -f $BASE_FILE -f $EVAL_FILE down"
note "  • Wipe data:  ${DC[*]} -f $BASE_FILE -f $EVAL_FILE down -v   (deletes volumes)"
