#!/usr/bin/env bash
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
# 1. Pre-upgrade backup
# ---------------------------------------------------------------------------
title "Pre-upgrade backup"
note "Running scripts/backup.sh — this is mandatory before pulling new images."
bash "$ROOT_DIR/scripts/backup.sh"
ok "backup complete"

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
    # Up to 60 polls x 10s = 10 minutes.
    for i in $(seq 1 60); do
      # Empty/no-output OR `{}`-only output → no active tasks. ``|| true`` so a
      # non-zero exit from inspect (broker unreachable, no workers) does not
      # abort the upgrade — we re-check at the end of the loop.
      active=$(docker-compose -f docker-compose.yml exec -T worker \
        celery -A tasks.celery_app inspect active --timeout=5 2>/dev/null || true)
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

marker = "   # removed in v2.4.0 (W6-#43d)"
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
docker-compose -f docker-compose.yml pull
ok "images pulled"

# ---------------------------------------------------------------------------
# 4. Drain removed-task names from the broker (W6-#43a)
# ---------------------------------------------------------------------------
# v2.4.0 removes the four Dependency-Track Celery tasks (trustedoss.dt_*).
# Any of those messages still queued in Redis when the new worker starts
# would hit ``NotRegistered``, NACK under ``task_acks_late=True``, and
# redeliver indefinitely. We purge them BEFORE the new image comes up so
# the new worker boots into a clean queue. Best-effort: ``|| true`` keeps
# the upgrade going if the worker container is already stopped or celery
# CLI is not present. NOTE: prod service is ``worker`` (docker-compose.yml);
# dev is ``celery-worker`` (docker-compose.dev.yml).
title "Draining removed DT tasks from the broker"
note "Purging trustedoss.dt_{resync,health,orphan_cleaner,orphan_cleanup}"
note "(in-flight DT messages would NACK forever against the new worker)."
docker-compose -f docker-compose.yml exec -T worker \
  celery -A tasks.celery_app purge -f \
    --task-names=trustedoss.dt_resync,trustedoss.dt_health,trustedoss.dt_orphan_cleaner,trustedoss.dt_orphan_cleanup \
    >/dev/null 2>&1 || true
ok "broker drain complete (best-effort)"

# ---------------------------------------------------------------------------
# 5. Recreate containers
# ---------------------------------------------------------------------------
title "Recreating containers"
note "The portal will be briefly unavailable (typically <30s)."
docker-compose -f docker-compose.yml up -d
ok "containers running"

# ---------------------------------------------------------------------------
# 6. alembic upgrade head
# ---------------------------------------------------------------------------
title "Database migration"
docker-compose -f docker-compose.yml exec -T backend alembic upgrade head
ok "schema is at HEAD"

# ---------------------------------------------------------------------------
# 7. Health probe
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
