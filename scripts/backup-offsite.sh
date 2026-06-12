#!/usr/bin/env bash
# TrustedOSS Portal — push local backups to an offsite object store (optional).
#
# scripts/backup.sh writes point-in-time backups under backups/<UTC-stamp>/.
# Those are LOCAL only, so a dead server loses them. This script copies the
# recent backup sets to an S3-compatible remote (Cloudflare R2 / Backblaze B2 /
# any rclone remote) so there is an offsite copy.
#
# It is OPT-IN and a SAFE NO-OP when unconfigured: with BACKUP_OFFSITE_REMOTE
# unset it prints a notice and exits 0, so it can be wired unconditionally (e.g.
# as an ExecStartPost on trustedoss-backup.service) without breaking local-only
# deployments.
#
# Configuration (.env, read at runtime per CLAUDE.md rule #11):
#   BACKUP_OFFSITE_REMOTE   rclone destination, e.g. "r2:trustedoss-backups".
#                           Empty / unset = offsite disabled (no-op).
#   BACKUP_OFFSITE_MAX_AGE  only copy backups newer than this (default 25h, so a
#                           daily run ships the latest set and skips old ones the
#                           remote already has). Any rclone duration (e.g. 25h).
#
# Prerequisite when enabled: `rclone` installed and a remote configured
# (`rclone config`). See docs/operator-runbook-hetzner.md §11.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
RESET='\033[0m'
ok()   { printf "${GREEN}✓${RESET} %s\n" "$1"; }
note() { printf "  %s\n" "$1"; }
warn() { printf "${YELLOW}!${RESET} %s\n" "$1" >&2; }
fail() { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }

# Pull config from .env without exporting the whole file. Runtime read (rule #11).
get_env() {
  [[ -f .env ]] || return 0
  grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true
}

remote="${BACKUP_OFFSITE_REMOTE:-$(get_env BACKUP_OFFSITE_REMOTE)}"
max_age="${BACKUP_OFFSITE_MAX_AGE:-$(get_env BACKUP_OFFSITE_MAX_AGE)}"
max_age="${max_age:-25h}"

if [[ -z "$remote" ]]; then
  note "BACKUP_OFFSITE_REMOTE is unset — offsite backup disabled (local-only). No-op."
  exit 0
fi

command -v rclone >/dev/null 2>&1 \
  || fail "BACKUP_OFFSITE_REMOTE is set but rclone is not installed. Install rclone and run 'rclone config' (see runbook §11)."

[[ -d backups ]] || fail "no backups/ directory yet — run scripts/backup.sh first."

note "Pushing backups newer than $max_age to $remote"
# copy (not sync): never deletes on the remote, so a local prune cannot cascade
# to the offsite copy. --max-age limits the transfer to the most recent set(s).
if rclone copy backups "$remote" --max-age "$max_age" --transfers 4 --checksum; then
  ok "offsite copy complete → $remote"
else
  fail "rclone copy failed — offsite backup NOT updated (local backup is unaffected)."
fi
