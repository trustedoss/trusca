#!/usr/bin/env bash
# TrustedOSS Portal — dev-stack reset.
#
# Tears the dev compose project down (including named volumes) and brings
# it back up clean. Use after a postgres data-corruption incident, a stale
# worker image (`aiosmtplib` ModuleNotFoundError), or to reclaim disk space
# pinned by abandoned dev volumes.
#
# Default behaviour:
#   - down -v        — stop services + drop named volumes for THIS project
#   - volume prune   — drop dangling volumes labelled with this compose project
#   - up -d          — rebuild containers, recreate volumes, run migrations
#                       (entrypoint of the backend service runs `alembic upgrade
#                       head` automatically)
#
# Optional flags:
#   --rebuild-worker   build the celery-worker image with --no-cache before up
#   --seed             run scripts/seed_e2e_user.py against the fresh stack
#   --no-prompt        skip the destructive-action confirmation
#
# CLAUDE.md compliance:
#   - core rule #10: docker-compose (V1, hyphen).
#   - All operations scoped to this compose project — does NOT touch other
#     stacks on the same host (`docker volume prune --filter` is project-
#     scoped).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="docker-compose.dev.yml"
PROJECT_LABEL="com.docker.compose.project=trustedoss-portal"

REBUILD_WORKER=0
SEED=0
NO_PROMPT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rebuild-worker) REBUILD_WORKER=1 ;;
    --seed) SEED=1 ;;
    --no-prompt) NO_PROMPT=1 ;;
    -h|--help)
      sed -n '1,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown flag: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "error: $COMPOSE_FILE not found at $ROOT_DIR" >&2
  exit 1
fi

if (( NO_PROMPT == 0 )); then
  echo "This will destroy all data in the dev stack (postgres, redis, dtrack volumes)."
  read -r -p "Continue? [y/N] " confirm
  case "$confirm" in
    y|Y|yes|YES) ;;
    *) echo "aborted"; exit 1 ;;
  esac
fi

echo "[1/4] docker-compose down -v"
docker-compose -f "$COMPOSE_FILE" down -v

echo "[2/4] docker volume prune (project-scoped)"
docker volume prune -f --filter "label=${PROJECT_LABEL}" >/dev/null

if (( REBUILD_WORKER == 1 )); then
  echo "[3/4] docker-compose build celery-worker --no-cache"
  docker-compose -f "$COMPOSE_FILE" build --no-cache celery-worker
else
  echo "[3/4] skip worker rebuild (pass --rebuild-worker to force)"
fi

echo "[4/4] docker-compose up -d"
docker-compose -f "$COMPOSE_FILE" up -d

if (( SEED == 1 )); then
  # Wait for backend to be healthy before seeding.
  for _ in $(seq 1 30); do
    state=$(docker-compose -f "$COMPOSE_FILE" ps --format json backend 2>/dev/null \
      | python3 -c "import sys,json; rows=[json.loads(l) for l in sys.stdin if l.strip()]; print((rows[0] if rows else {}).get('Health','none'))" \
      || echo "none")
    if [[ "$state" == "healthy" ]]; then break; fi
    sleep 2
  done
  echo "seeding e2e user…"
  docker-compose -f "$COMPOSE_FILE" exec -T backend python scripts/seed_e2e_user.py
fi

echo "done. dev stack reset complete."
