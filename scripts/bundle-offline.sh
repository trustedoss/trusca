#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TRUSCA - offline install bundle builder (#400).
#
# Run on a CONNECTED host (build server, developer laptop, a bastion with
# internet egress). Packages everything an air-gapped `scripts/install.sh
# --offline <bundle.tar>` run needs so the target host never has to reach a
# registry or the internet:
#
#   1. `docker save` of the six images docker-compose.yml actually pulls -
#      traefik, postgres, redis, trusca-backend, trusca-backend-worker,
#      trusca-frontend. The originating issue (#400) says "four images";
#      measured against docker-compose.yml, six distinct image references
#      are pulled (the backend-worker image is reused three times, for
#      worker-scan / worker-default / beat, but that is still one `docker
#      save` entry). An air-gapped `docker-compose up` needs the whole set,
#      not an approximation of it, so this script bundles all six.
#   2. A Trivy vulnerability DB snapshot (+ best-effort Java DB) via
#      `trivy --download-db-only` / `--download-java-db-only`, so a fresh
#      worker does not need `ghcr.io/aquasecurity/trivy-db` reachability.
#   3. The Helm chart (charts/trustedoss/), for a Helm-based air-gapped
#      deploy.
#   4. docker-compose.yml, docker-compose.dev.yml, and .env.example, so the
#      bundle is useful even without a fresh git clone alongside it.
#
# Usage:
#   bash scripts/bundle-offline.sh
#   bash scripts/bundle-offline.sh --tag 0.22.0 --registry ghcr.io/trustedoss
#   bash scripts/bundle-offline.sh -o /path/to/trusca-offline-bundle.tar
#
# IMAGE_TAG / IMAGE_REGISTRY default from .env when present (same convention
# as backup.sh / install.sh), else fall back to docker-compose.yml's own
# `${IMAGE_TAG:-...}` defaults. --tag / --registry override both.
#
# The output is a single, uncompressed tar (images and the Trivy DB are
# already compressed internally; re-gzipping a multi-GB tar mostly just
# burns CPU for little size win). On any failure this script cleans up its
# scratch directory and never leaves a partial bundle at the final path -
# the tar is written to a `.partial` sibling and `mv`d into place only after
# everything above succeeded.
#
# CLAUDE.md compliance:
#   - core rule #9 : every image reference below carries an explicit tag -
#     never the floating "latest" tag.
#   - core rule #10: docker-compose (V1, hyphen) is the only compose form
#     referenced (in comments / follow-up instructions); this script itself
#     only calls `docker` / `trivy` / `tar`, not compose.
#   - core rule #11: IMAGE_TAG / IMAGE_REGISTRY are read from the
#     environment / .env at run time, never baked into this script.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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
# 0. CLI flag parsing
# ---------------------------------------------------------------------------
CLI_TAG=""
CLI_REGISTRY=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      [[ $# -ge 2 ]] || fail "--tag requires a value"
      CLI_TAG="$2"
      shift 2
      ;;
    --tag=*) CLI_TAG="${1#--tag=}"; shift ;;
    --registry)
      [[ $# -ge 2 ]] || fail "--registry requires a value"
      CLI_REGISTRY="$2"
      shift 2
      ;;
    --registry=*) CLI_REGISTRY="${1#--registry=}"; shift ;;
    -o|--output)
      [[ $# -ge 2 ]] || fail "--output requires a value"
      OUTPUT="$2"
      shift 2
      ;;
    --output=*) OUTPUT="${1#--output=}"; shift ;;
    -h|--help)
      cat <<USAGE
Usage: bash scripts/bundle-offline.sh [--tag <tag>] [--registry <registry>] [-o|--output <path>]

  --tag <tag>          Override IMAGE_TAG (default: from .env, else 0.12.0).
  --registry <reg>     Override IMAGE_REGISTRY (default: from .env, else
                        ghcr.io/trustedoss).
  -o, --output <path>  Output tar path (default:
                        trusca-offline-bundle-<tag>.tar in the repo root).
USAGE
      exit 0
      ;;
    *)
      printf '✗ unknown argument: %s (try --help)\n' "$1" >&2
      exit 2
      ;;
  esac
done

command -v docker >/dev/null 2>&1 || fail "docker is required (docker save)."
command -v tar >/dev/null 2>&1 || fail "tar is required."

# sha256sum (GNU coreutils, the install target per this script's own header)
# or shasum -a 256 (macOS / BSD, for building a bundle on a dev laptop) -
# whichever is on PATH. Security review finding #400-S1: without a checksum
# alongside the tar, `install.sh --offline` had no way to detect a bit-flipped
# or tampered bundle before `docker load`-ing whatever was inside it.
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  else
    fail "neither sha256sum nor shasum is available - cannot checksum the bundle."
  fi
}

# ---------------------------------------------------------------------------
# 1. Resolve IMAGE_REGISTRY / IMAGE_TAG - same precedence as install.sh /
#    backup.sh: .env (if present) provides the deployment's real values,
#    then an explicit --tag/--registry flag overrides it, then the
#    docker-compose.yml fallback default is the last resort.
# ---------------------------------------------------------------------------
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a; . ./.env; set +a
fi
IMAGE_REGISTRY="${CLI_REGISTRY:-${IMAGE_REGISTRY:-ghcr.io/trustedoss}}"
IMAGE_TAG="${CLI_TAG:-${IMAGE_TAG:-0.12.0}}"

# The six images docker-compose.yml actually pulls (measured, not the
# issue's approximate "four images" - see the header comment). The worker
# image is one `docker save` entry even though three services
# (worker-scan / worker-default / beat) reuse it.
IMAGES=(
  "traefik:v3.6.1"
  "postgres:17.2-alpine"
  "redis:7.4-alpine"
  "${IMAGE_REGISTRY}/trusca-backend:${IMAGE_TAG}"
  "${IMAGE_REGISTRY}/trusca-backend-worker:${IMAGE_TAG}"
  "${IMAGE_REGISTRY}/trusca-frontend:${IMAGE_TAG}"
)

OUTPUT="${OUTPUT:-$ROOT_DIR/trusca-offline-bundle-${IMAGE_TAG}.tar}"
PARTIAL_OUTPUT="${OUTPUT}.partial"

title "Offline bundle"
note "IMAGE_REGISTRY=$IMAGE_REGISTRY"
note "IMAGE_TAG=$IMAGE_TAG"
note "output=$OUTPUT"

# ---------------------------------------------------------------------------
# 2. Scratch workspace + cleanup - never leave a partial bundle behind.
# ---------------------------------------------------------------------------
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/trusca-bundle-offline.XXXXXX")"
cleanup() {
  local ec=$?
  rm -rf "$WORKDIR"
  # A `.partial` file only survives a clean run for the instant between
  # `tar -cf` and `mv` below - if we get here with it still present, the run
  # failed (or was interrupted) mid-write and it is garbage, not a bundle.
  [[ -f "$PARTIAL_OUTPUT" ]] && rm -f "$PARTIAL_OUTPUT"
  exit "$ec"
}
trap cleanup EXIT

mkdir -p "$WORKDIR/images" "$WORKDIR/trivy-db" "$WORKDIR/charts" "$WORKDIR/compose"

# ---------------------------------------------------------------------------
# 3. docker save - pull locally first if an image is not already present.
# ---------------------------------------------------------------------------
title "Saving ${#IMAGES[@]} images"
for img in "${IMAGES[@]}"; do
  if ! docker image inspect "$img" >/dev/null 2>&1; then
    note "pulling $img (not present locally)"
    docker pull "$img" \
      || fail "failed to pull $img - this host needs internet egress and the image/tag must exist"
  fi
  ok "have $img"
done

docker save -o "$WORKDIR/images/images.tar" "${IMAGES[@]}" \
  || fail "docker save failed"
ok "saved images -> images/images.tar ($(du -h "$WORKDIR/images/images.tar" | cut -f1))"

# ---------------------------------------------------------------------------
# 4. Trivy DB (+ best-effort Java DB)
# ---------------------------------------------------------------------------
title "Downloading the Trivy vulnerability DB"
if command -v trivy >/dev/null 2>&1; then
  if TRIVY_CACHE_DIR="$WORKDIR/trivy-db" trivy image --download-db-only --quiet --no-progress; then
    ok "trivy DB downloaded -> trivy-db/ ($(du -sh "$WORKDIR/trivy-db" | cut -f1))"
  else
    warn "trivy --download-db-only failed - check egress to ghcr.io/aquasecurity/trivy-db (or TRIVY_DB_REPOSITORY)."
    warn "the bundle will still be built WITHOUT a Trivy DB snapshot; install.sh --offline falls back to the worker's normal online bootstrap."
  fi

  # Java DB - Trivy's separate GAV-lookup database for jar analysis. Not
  # every trivy build ships this flag, and it is a smaller nice-to-have than
  # the vulnerability DB above, so a failure here is a warning, not fatal.
  if TRIVY_CACHE_DIR="$WORKDIR/trivy-db" trivy image --download-java-db-only --quiet --no-progress 2>"$WORKDIR/.java-db-stderr.log"; then
    ok "trivy Java DB downloaded"
  else
    warn "trivy --download-java-db-only skipped/failed (non-fatal - see $WORKDIR/.java-db-stderr.log while this script is still running). Maven/Gradle jar-to-package matching may fall back to filename heuristics offline."
  fi
else
  warn "trivy not found on PATH - the bundle will ship WITHOUT a Trivy DB snapshot."
  warn "install trivy (https://aquasecurity.github.io/trivy/) and re-run to include one, or accept the worker's normal online bootstrap on the target host."
fi

# ---------------------------------------------------------------------------
# 5. Helm chart + Compose files
# ---------------------------------------------------------------------------
title "Bundling the Helm chart and Compose files"
[[ -d "$ROOT_DIR/charts/trustedoss" ]] || fail "charts/trustedoss not found - run this from the repo root."
cp -a "$ROOT_DIR/charts/trustedoss" "$WORKDIR/charts/trustedoss"
ok "copied charts/trustedoss"

for f in docker-compose.yml docker-compose.dev.yml .env.example; do
  [[ -f "$ROOT_DIR/$f" ]] || fail "$f not found - run this from the repo root."
  cp "$ROOT_DIR/$f" "$WORKDIR/compose/$f"
done
ok "copied docker-compose.yml, docker-compose.dev.yml, .env.example"

# ---------------------------------------------------------------------------
# 6. Manifest
# ---------------------------------------------------------------------------
{
  printf 'TRUSCA offline install bundle\n'
  printf 'Created: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'IMAGE_REGISTRY=%s\n' "$IMAGE_REGISTRY"
  printf 'IMAGE_TAG=%s\n' "$IMAGE_TAG"
  printf 'Images:\n'
  for img in "${IMAGES[@]}"; do printf '  - %s\n' "$img"; done
  printf 'Trivy DB included: %s\n' "$([[ -f "$WORKDIR/trivy-db/db/metadata.json" ]] && echo yes || echo no)"
} > "$WORKDIR/MANIFEST.txt"
ok "wrote MANIFEST.txt"

# ---------------------------------------------------------------------------
# 7. Pack - atomic: write to a .partial sibling, then mv into place.
# ---------------------------------------------------------------------------
title "Packing the bundle"
tar -cf "$PARTIAL_OUTPUT" -C "$WORKDIR" .
mv "$PARTIAL_OUTPUT" "$OUTPUT"
ok "wrote $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"

# ---------------------------------------------------------------------------
# 8. Checksum sidecar - security review finding #400-S1. Written AFTER the
# mv above, over the FINAL bytes at $OUTPUT, so the hash covers exactly what
# a later `install.sh --offline` will read. `<hash>  <basename>` (two spaces)
# is plain `sha256sum` output format, so `sha256sum -c bundle.tar.sha256` in
# the bundle's own directory also verifies it manually.
# ---------------------------------------------------------------------------
CHECKSUM_FILE="${OUTPUT}.sha256"
printf '%s  %s\n' "$(sha256_of "$OUTPUT")" "$(basename "$OUTPUT")" > "$CHECKSUM_FILE"
ok "wrote $CHECKSUM_FILE"

title "Contents"
tar -tf "$OUTPUT" | sed 's/^/  /'

title "Offline bundle complete"
printf "  %s\n" "$OUTPUT"
printf "  %s\n" "$CHECKSUM_FILE"
note "Install with: bash scripts/install.sh --offline $OUTPUT"
note "Keep the .sha256 file next to the bundle - install.sh --offline requires it."
