#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TrustedOSS Portal — upgrade the running stack to a newer image set.
#
# Flow:
#   1. Take a pre-upgrade backup (always — safety net).
#   1.5 .env append-only sync (no destructive edits to operator values).
#   2. (v2.3 → v2.4 ONLY) DT migration prelude — drain Celery queue, stop the
#      dtrack-api container, optionally archive its volume, comment out DT_*
#      keys in .env, append TRIVY_* defaults. Skipped on v2.x → v2.x where no
#      DT artefacts are detected.
#   3. Pull the new images defined in docker-compose.yml.
#   4. Purge removed DT task names from the broker (NACK-loop guard).
#   5. up -d  — Compose recreates only services whose image hash changed.
#   6. Run alembic upgrade head.
#   7. Wait for /health to return 200.
#
# CLAUDE.md compliance:
#   - core rule #6 : Alembic forward-only. Rollback path = restore.sh.
#   - core rule #10: docker-compose (V1, hyphenated).

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
note()  { printf "  %s\n" "$1"; }
title() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

command -v docker-compose >/dev/null 2>&1 || fail "docker-compose (V1) is required."

# ---------------------------------------------------------------------------
# 0. Compose file selection — follow the deploy's own overlay
# ---------------------------------------------------------------------------
# Every docker-compose call below used to hard-code `-f docker-compose.yml`,
# which silently DROPS whatever overlay the deployment actually runs with. An
# explicit `-f` also overrides the standard COMPOSE_FILE variable, so declaring
# the overlay the documented way had no effect here.
#
# That is not cosmetic. The demo host runs `docker-compose.yml` +
# `docker-compose.demo.yml`, and the overlay is what passes DEMO_READ_ONLY into
# the backend and what caps the worker at the box's 2 CPUs. Upgrading through
# the base file alone rebuilt the stack WITHOUT the public read-only lock and
# with the 4.0 CPU default — a deploy quietly turning off a safety boundary.
#
# COMPOSE_FILE is read from .env when the environment does not already carry
# it, because that is where an operator declares it and where docker-compose
# itself looks. Unset (the single-file default) keeps the previous behaviour.
if [ -z "${COMPOSE_FILE:-}" ] && [ -f .env ]; then
  COMPOSE_FILE="$(grep -E '^COMPOSE_FILE=' .env | tail -1 | cut -d= -f2- || true)"
  [ -n "$COMPOSE_FILE" ] && export COMPOSE_FILE
fi
COMPOSE_ARGS=(-f docker-compose.yml)
if [ -n "${COMPOSE_FILE:-}" ]; then
  COMPOSE_ARGS=()
  IFS=':' read -ra _compose_files <<< "$COMPOSE_FILE"
  for _f in "${_compose_files[@]}"; do
    [ -n "$_f" ] && COMPOSE_ARGS+=(-f "$_f")
  done
  [ ${#COMPOSE_ARGS[@]} -eq 0 ] && COMPOSE_ARGS=(-f docker-compose.yml)
fi
note "compose files: ${COMPOSE_ARGS[*]}"

# ---------------------------------------------------------------------------
# 0.5 Version guard - refuse a downgrade or a skipped major (ER15)
# ---------------------------------------------------------------------------
# Alembic is forward-only (CLAUDE.md core rule #6): there is no downgrade path,
# so pointing this script at an OLDER image set does not roll anything back. It
# starts old code against a newer schema, which fails in ways a restore is the
# only exit from. Skipping a major has the same shape one step removed, since
# the release notes for the major in between are where the manual steps live
# (the v2.3 to v2.4 prelude below is exactly such a step).
#
# The running version is read from the backend container's own image tag rather
# than from the API: /health carries no version and /v1/about needs a login,
# neither of which an unattended upgrade has. When either side cannot be
# determined this warns and continues, because refusing to upgrade over a
# parsing failure would be worse than the risk it guards.
title "Version check"

# Prints "<major> <minor> <patch>" for an X.Y.Z tag, dropping any prerelease or
# build suffix. Returns non-zero for anything that is not a version.
semver_parts() {
  local raw="${1#v}"
  raw="${raw%%-*}"
  raw="${raw%%+*}"
  case "$raw" in
    [0-9]*.[0-9]*.[0-9]*) ;;
    *) return 1 ;;
  esac
  local major minor patch
  IFS=. read -r major minor patch <<< "$raw"
  case "${major}${minor}${patch}" in
    *[!0-9]*) return 1 ;;
  esac
  printf '%s %s %s\n' "$major" "$minor" "$patch"
}

running_version=""
backend_cid="$(docker-compose "${COMPOSE_ARGS[@]}" ps -q backend 2>/dev/null || true)"
if [ -n "$backend_cid" ]; then
  running_image="$(docker inspect -f '{{.Config.Image}}' "$backend_cid" 2>/dev/null || true)"
  if [ -n "$running_image" ] && [ "${running_image##*:}" != "$running_image" ]; then
    running_version="${running_image##*:}"
  fi
fi

target_version="${IMAGE_TAG:-}"
if [ -z "$target_version" ] && [ -f .env ]; then
  target_version="$(grep -E '^IMAGE_TAG=' .env | tail -1 | cut -d= -f2- || true)"
fi

if [ -z "$running_version" ] || [ -z "$target_version" ]; then
  warn "could not determine both versions - skipping the version check."
  note "running: ${running_version:-unknown}, target: ${target_version:-unknown}"
elif ! running_parts="$(semver_parts "$running_version")" ||      ! target_parts="$(semver_parts "$target_version")"; then
  warn "one of the versions is not an X.Y.Z tag - skipping the version check."
  note "running: ${running_version}, target: ${target_version}"
else
  read -r run_major run_minor run_patch <<< "$running_parts"
  read -r tgt_major tgt_minor tgt_patch <<< "$target_parts"
  note "running ${running_version} -> target ${target_version}"

  running_num=$(( run_major * 1000000 + run_minor * 1000 + run_patch ))
  target_num=$(( tgt_major * 1000000 + tgt_minor * 1000 + tgt_patch ))

  if [ "${UPGRADE_ALLOW_VERSION_SKIP:-false}" = "true" ]; then
    warn "UPGRADE_ALLOW_VERSION_SKIP=true - version checks bypassed."
  elif [ "$target_num" -lt "$running_num" ]; then
    fail "refusing to move from ${running_version} DOWN to ${target_version}. Migrations are forward-only, so this is not a rollback; to go back, restore a backup with scripts/restore.sh. Set UPGRADE_ALLOW_VERSION_SKIP=true only if you know the schema is unchanged between the two."
  elif [ "$tgt_major" -gt "$(( run_major + 1 ))" ]; then
    fail "refusing to skip a major version (${running_version} -> ${target_version}). Upgrade one major at a time so each major's release notes and migration prelude run. Set UPGRADE_ALLOW_VERSION_SKIP=true to override."
  elif [ "$target_num" -eq "$running_num" ]; then
    note "already at ${target_version} - continuing (image digests may still differ)."
  fi
  ok "version check passed"
fi

# ---------------------------------------------------------------------------
# 1. Pre-upgrade backup
# ---------------------------------------------------------------------------
# Mandatory while there is a live database to dump — and skipped when there is
# not. backup.sh runs `pg_dump` through `docker-compose exec postgres`, which
# exits non-zero with "service \"postgres\" is not running" against a stopped
# stack. Failing here used to be unrecoverable in the one situation that needs
# this script most: a previous deploy that died AFTER stopping the containers
# left the stack down, and the next upgrade could not get past its own backup
# step to bring it back. A stopped database also has nothing to lose, so the
# safety net protects nothing here.
title "Pre-upgrade backup"
pg_cid="$(docker-compose "${COMPOSE_ARGS[@]}" ps -q postgres 2>/dev/null || true)"
if [ -n "$pg_cid" ] && \
   [ "$(docker inspect -f '{{.State.Running}}' "$pg_cid" 2>/dev/null || echo false)" = "true" ]; then
  note "Running scripts/backup.sh — this is mandatory before pulling new images."
  bash "$ROOT_DIR/scripts/backup.sh"
  ok "backup complete"
else
  warn "postgres is not running — skipping the pre-upgrade backup."
  note "There is no live database to dump. Continuing so a stack that is"
  note "already down can be brought back up."
fi

# ---------------------------------------------------------------------------
# 1.4 Secret pre-flight
# ---------------------------------------------------------------------------
# Checked BEFORE the pull, because the backend refuses to start on a template
# SECRET_KEY outside dev and the entrypoint applies Alembic migrations first.
# Without this the stack ends up migrated and crash-looping, and the only clue
# is in `docker-compose logs`. Here it is a message on the operator's terminal
# with the stack still up. The marker list mirrors
# apps/backend/core/config.py's _PLACEHOLDER_SECRET_MARKERS, pinned by
# apps/backend/tests/unit/test_placeholder_secret_rejection.py.
title "Secret pre-flight"
if [ -f .env ]; then
  preflight_app_env="$(sed -n 's/^APP_ENV=\(.*\)$/\1/p' .env | tail -n 1)"
  preflight_secret="$(sed -n 's/^SECRET_KEY=\(.*\)$/\1/p' .env | tail -n 1)"
  if [ "${preflight_app_env:-prod}" = "dev" ]; then
    note "APP_ENV=dev, so the placeholder guard does not apply."
  elif [ -z "$preflight_secret" ]; then
    fail "SECRET_KEY is empty in .env, and the backend will not start outside dev. Generate one with 'openssl rand -hex 32'."
  elif printf '%s' "$preflight_secret" | tr '[:upper:]' '[:lower:]' \
       | grep -qE 'change|replace|placeholder|example|your-secret|your_secret|yoursecret|insecure|do-not-use|donotuse|dev-only|min-32-chars'; then
    fail "SECRET_KEY in .env is a template value, not a generated one. The backend will refuse to start. Replace it with 'openssl rand -hex 32' output and re-run."
  else
    ok "SECRET_KEY looks like generated key material"
  fi
else
  warn ".env not found, skipping the secret pre-flight."
fi

# ---------------------------------------------------------------------------
# 1.5 .env sync — append-only (W6-chore-seed B)
# ---------------------------------------------------------------------------
title "Environment sync"
# shellcheck source=scripts/lib/env_sync.sh
source "$ROOT_DIR/scripts/lib/env_sync.sh"
env_append_only_sync .env.example .env
ok "env sync complete (existing values preserved)"

# ---------------------------------------------------------------------------
# 2. v2.3 → v2.4 migration prelude (DT removal — ADR-0001 / W6-#43d)
# ---------------------------------------------------------------------------
# v2.4.0 removes Dependency-Track. Detect a v2.3 deployment by ANY of:
#   * `DT_URL` or `DT_API_KEY` set (non-comment, non-empty) in .env
#   * a `dtrack-api` container present (running or stopped)
# When detected, run the 5-step migration prelude BEFORE we pull v2.4 images
# (an in-flight scan + a swap to a worker image that no longer knows the DT
# tasks would NACK forever; the broker drain step below catches stragglers).
# On a v2.x→v2.x upgrade (no DT trace) we skip this section entirely.
title "v2.3 → v2.4 migration check (Dependency-Track removal)"

# Detect DT_URL / DT_API_KEY set (skip lines starting with '#' or whitespace+#).
dt_env_set=0
if grep -E '^[[:space:]]*(DT_URL|DT_API_KEY)=[^[:space:]]' .env >/dev/null 2>&1; then
  dt_env_set=1
fi
# Detect a dtrack-api container (running OR stopped — `docker ps -a -q`).
dt_container=""
if command -v docker >/dev/null 2>&1; then
  dt_container=$(docker ps -a --filter "name=dtrack-api" --format "{{.Names}}" 2>/dev/null | head -1 || true)
fi

if [[ $dt_env_set -eq 1 || -n "$dt_container" ]]; then
  note "v2.3 artefacts detected — running 5-step DT removal prelude."
  if [[ $dt_env_set -eq 1 ]]; then note "  - .env: DT_URL / DT_API_KEY set"; fi
  if [[ -n "$dt_container" ]]; then note "  - container: $dt_container present"; fi

  # ── 2.1 Drain Celery queue ────────────────────────────────────────────────
  # Wait for the active-task list to be empty so an upgrade does not interrupt
  # a running scan. Best-effort: if the worker container is already stopped /
  # the celery CLI is absent we WARN and continue (the broker purge in step 4
  # cleans up whatever is left behind).
  title "Step 2.1 — Draining the Celery queue (in-flight scan protection)"
  # Allow caller to skip the wait entirely (CI / forced upgrade).
  if [[ "${UPGRADE_SKIP_DRAIN:-0}" == "1" ]]; then
    warn "UPGRADE_SKIP_DRAIN=1 — skipping queue drain"
  else
    note "Polling \`celery inspect active\` for up to 10 minutes (set UPGRADE_SKIP_DRAIN=1 to skip)."
    drained=0
    # S3 (concurrency-scaling-plan-2026-08-22.md §3.2/§4): the single
    # `worker` service split into `worker-scan` and `worker-default` - poll
    # BOTH, since an in-flight task can be active on either one.
    # Up to 60 polls x 10s = 10 minutes.
    for i in $(seq 1 60); do
      active=""
      for svc in worker-scan worker-default; do
        # Empty/no-output OR `{}`-only output → no active tasks. ``|| true``
        # so a non-zero exit from inspect (broker unreachable, no workers,
        # or a pre-split .env that no longer has this service) does not
        # abort the upgrade - we re-check at the end of the loop.
        svc_active=$(docker-compose "${COMPOSE_ARGS[@]}" exec -T "$svc" \
          celery -A tasks.celery_app inspect active --timeout=5 2>/dev/null || true)
        active="${active}${svc_active}"
      done
      # `inspect active` prints "- empty -" when there are no tasks, OR an
      # `<worker>: OK` line followed by `- empty -`. Treat both empties OR an
      # absent worker (no output at all) as drained.
      if [[ -z "$active" ]] || echo "$active" | grep -qE 'empty|^[[:space:]]*-[[:space:]]+$'; then
        drained=1
        break
      fi
      note "  still active — sleeping 10s (try $i/60)"
      sleep 10
    done
    if [[ $drained -eq 1 ]]; then
      ok "Celery queue is drained"
    else
      warn "queue still has active tasks after 10 minutes."
      if [[ "${NO_PROMPT:-0}" == "1" ]]; then
        warn "non-interactive — continuing anyway (UPGRADE_SKIP_DRAIN to silence this warning)"
      else
        read -r -p "Continue anyway? [y/N] " reply
        if [[ ! "${reply:-N}" =~ ^[Yy]$ ]]; then
          fail "aborted by operator — re-run when the queue is empty, or set UPGRADE_SKIP_DRAIN=1"
        fi
      fi
    fi
  fi

  # ── 2.2 Stop & remove the dtrack-api container (volume preserved) ─────────
  title "Step 2.2 — Stopping the dtrack-api container"
  if [[ -n "$dt_container" ]]; then
    # `rm -f -s` stops then removes — the named volume `trustedoss_dt-data`
    # (or whatever was used in your overlay) is INTENTIONALLY left in place so
    # 2.3 can archive it before the operator decides whether to drop it.
    docker rm -f "$dt_container" >/dev/null 2>&1 || true
    ok "dtrack-api container removed (volume preserved for archive)"
  else
    note "no dtrack-api container present — skipping"
  fi

  # ── 2.3 Optional DT volume archive ────────────────────────────────────────
  # docker volume name varies (trustedoss_dt-data / dt-data / a docker stack
  # prefix). We probe for the well-known prefix patterns and prompt the
  # operator. Default = N (do not delete). When the operator says yes we
  # tarball it under ./backup/ and leave deletion for later.
  title "Step 2.3 — Archive the DT data volume? (no deletion — backup only)"
  dt_volume=$(docker volume ls --format '{{.Name}}' 2>/dev/null \
    | grep -E '(^|_)dt-data($|[-_])|dtrack[-_]data' | head -1 || true)
  if [[ -n "$dt_volume" ]]; then
    note "Detected DT volume: $dt_volume"
    if [[ "${NO_PROMPT:-0}" == "1" ]]; then
      do_archive=0
      warn "non-interactive — skipping DT volume archive (preserved as-is)"
    else
      read -r -p "Archive '$dt_volume' to ./backup/ ? [y/N] " reply
      reply=${reply:-N}
      [[ "$reply" =~ ^[Yy]$ ]] && do_archive=1 || do_archive=0
    fi
    if [[ $do_archive -eq 1 ]]; then
      mkdir -p backup
      archive="backup/dt-volume-$(date +%Y%m%d-%H%M%S).tar.gz"
      # Use a throw-away busybox container to tar the volume mount-point.
      if docker run --rm \
          -v "${dt_volume}":/data:ro \
          -v "$ROOT_DIR/backup":/backup \
          busybox:1.36 \
          tar -C /data -czf "/backup/$(basename "$archive")" . 2>/dev/null; then
        ok "DT volume archived → $archive"
        note "(volume is NOT deleted — \`docker volume rm $dt_volume\` when you are ready)"
      else
        warn "archive failed — DT volume is unchanged"
      fi
    else
      note "DT volume preserved as-is — drop later with \`docker volume rm $dt_volume\`"
    fi
  else
    note "no recognisable DT volume detected — skipping archive"
  fi

  # ── 2.4 Comment out DT_* keys in .env + .env.example ──────────────────────
  # forward-only: we never DELETE the operator's lines (they may want to
  # consult the values later), just prepend `# ` so v2.4 ignores them and
  # the file stays diff-friendly. Idempotent: re-running just no-ops.
  title "Step 2.4 — Commenting out DT_* keys in .env"
  python3 - <<'PYTHON'
import re
from pathlib import Path

marker = "   # removed in v2.4.0"
for path in (".env", ".env.example"):
    p = Path(path)
    if not p.exists():
        continue
    text = p.read_text()
    # Match a line starting with DT_<UPPER>=<anything>, NOT already
    # commented. Append the marker only on the first commenting (idempotent).
    def _comment(m: re.Match) -> str:
        line = m.group(0)
        if marker.strip() in line:
            return line  # already migrated, no-op
        return "# " + line + marker
    new = re.sub(r"^(DT_[A-Z_]+=.*)$", _comment, text, flags=re.M)
    if new != text:
        p.write_text(new)
        print(f"  updated: {path}")
PYTHON
  ok "DT_* keys in .env (and .env.example) are commented out"

  # ── 2.5 Reminder: 1-click full re-match (admin UI) ────────────────────────
  title "Step 2.5 — Post-upgrade reminder"
  note "After the upgrade completes, the worker downloads the Trivy DB on"
  note "first boot (1-3 minutes) and the rematch beat re-walks every"
  note "project's most-recent SBOM on its 6-hourly schedule."
  note ""
  note "To force an IMMEDIATE re-match of every project:"
  note "  1. open /admin/health in the portal"
  note "  2. click \"Trigger full re-match\" (W6-#43e — lands in v2.4.0 GA)"
  ok "v2.3 → v2.4 prelude complete"
else
  note "no DT artefacts detected — skipping v2.3 → v2.4 prelude."
fi

# ---------------------------------------------------------------------------
# 3. Pull new images
# ---------------------------------------------------------------------------
title "Pulling new images"
docker-compose "${COMPOSE_ARGS[@]}" pull
ok "images pulled"

# ---------------------------------------------------------------------------
# 4. Drain removed-task names from the broker
# ---------------------------------------------------------------------------
# v2.4.0 removes the four Dependency-Track Celery tasks (trustedoss.dt_*).
# Any of those messages still queued in Redis when the new worker starts
# would hit ``NotRegistered``, NACK under ``task_acks_late=True``, and
# redeliver indefinitely. We purge them BEFORE the new image comes up so
# the new worker boots into a clean queue. Best-effort: ``|| true`` keeps
# the upgrade going if the worker container is already stopped or celery
# CLI is not present. `celery purge` is a broker-side operation (it does not
# matter which container issues it, only that it can reach the same Redis
# broker), so any one worker service works - we use `worker-default`, the
# service these non-scan legacy tasks would route to today. NOTE: prod
# services are ``worker-scan`` / ``worker-default`` (S3,
# docker-compose.yml); dev is still the single ``celery-worker``
# (docker-compose.dev.yml, not split by S3).
title "Draining removed DT tasks from the broker"
note "Purging trustedoss.dt_{resync,health,orphan_cleaner,orphan_cleanup}"
note "(in-flight DT messages would NACK forever against the new worker)."
docker-compose "${COMPOSE_ARGS[@]}" exec -T worker-default \
  celery -A tasks.celery_app purge -f \
    --task-names=trustedoss.dt_resync,trustedoss.dt_health,trustedoss.dt_orphan_cleaner,trustedoss.dt_orphan_cleanup \
    >/dev/null 2>&1 || true
ok "broker drain complete (best-effort)"

# ---------------------------------------------------------------------------
# 4.5 Worker CPU limit — clamp to the host's online CPU count
# ---------------------------------------------------------------------------
# Same clamp install.sh applies at 2c, repeated here because an .env can reach
# this point without it: installs that predate that step, hand-written files,
# or a host that was resized down. docker-compose.yml caps worker-scan at
# `${WORKER_CPU_LIMIT:-4.0}` (S3: this env var kept its pre-split name and
# now applies specifically to worker-scan, the heavier of the two split
# services; worker-default's own `WORKER_DEFAULT_CPU_LIMIT` defaults to a
# small enough value, 1.0, that it does not need this clamp), and Compose V2
# treats a cpus limit above the host's online CPU count as a HARD error at
# `up` ("range of CPUs is from 0.01 to N") - so the stock 4.0 aborts the
# recreate below on a 2-vCPU box, midway through, with services already
# stopped.
title "Worker CPU limit"
host_cpus=$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 4)
case "$host_cpus" in ''|*[!0-9]*) host_cpus=4 ;; esac
if [ "$host_cpus" -lt 4 ]; then worker_cpu_limit="$host_cpus"; else worker_cpu_limit="4"; fi
python3 - "$worker_cpu_limit" <<'PYTHON'
import re, sys
from pathlib import Path
val = sys.argv[1]
env = Path(".env")
text = env.read_text()
line = f"WORKER_CPU_LIMIT={val}"
pat = r"^WORKER_CPU_LIMIT=.*$"
text = re.sub(pat, line, text, flags=re.M) if re.search(pat, text, flags=re.M) else text.rstrip() + f"\n{line}\n"
env.write_text(text)
PYTHON
ok "WORKER_CPU_LIMIT=${worker_cpu_limit} (host has ${host_cpus} online CPU(s))"

# ---------------------------------------------------------------------------
# 5. Recreate containers
# ---------------------------------------------------------------------------
title "Recreating containers"
note "The portal will be briefly unavailable (typically <30s)."
docker-compose "${COMPOSE_ARGS[@]}" up -d
ok "containers running"

# ---------------------------------------------------------------------------
# 6. alembic upgrade head
# ---------------------------------------------------------------------------
title "Database migration"
docker-compose "${COMPOSE_ARGS[@]}" exec -T backend alembic upgrade head
ok "schema is at HEAD"

# ---------------------------------------------------------------------------
# 7. Health probe
# ---------------------------------------------------------------------------
title "Post-upgrade health probe"
for _ in $(seq 1 30); do
  if docker-compose "${COMPOSE_ARGS[@]}" exec -T backend curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    ok "backend is healthy"
    title "Upgrade complete"
    note "If something looks off, restore the pre-upgrade backup:"
    note "  bash scripts/restore.sh \$(ls -td backups/* | head -1)"
    exit 0
  fi
  sleep 2
done
fail "backend did not become healthy. Inspect: docker-compose ${COMPOSE_ARGS[*]} logs backend"
