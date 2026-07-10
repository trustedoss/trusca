#!/bin/sh
# build_prep_source.sh — runs INSIDE a per-language cdxgen sidecar.
#
#   Usage: sh build_prep_source.sh <SRC_DIR> <OUTPUT_FILE> [SPEC_VERSION]
#
# General multi-language build-prep, adapted from BomLens docker/lib/build-prep.sh
# (Apache-2.0). cdxgen does not auto-resolve transitive deps for some ecosystems
# (notably Rust / Go) without a populated lockfile, so we generate it first, then
# run cdxgen. Each step is gated on the relevant manifest AND the resolver being
# present in the image, so the same script is a no-op for languages the chosen
# image does not carry. Passed to the sidecar inline via `sh -c "<script>"`.
#
# POSIX sh. Best-effort: prep never fails the scan (set +e); an empty SBOM is a
# degraded (not fatal) outcome.
set +e

SRC="${1:-/app}"
OUT="${2:-$SRC/bom.json}"
SPEC="${3:-1.5}"
mkdir -p "${HOME:-/tmp/sbomhome}" 2>/dev/null || true
cd "$SRC" 2>/dev/null || exit 0

log() { echo "[build-prep] $*"; }

# Rust — cdxgen does NOT auto-run cargo; the lockfile is essential for transitive.
if [ -f Cargo.toml ] && command -v cargo >/dev/null 2>&1; then
  log "cargo generate-lockfile"
  cargo generate-lockfile 2>/dev/null
fi

# Go — complete go.sum so cdxgen's default-readonly `go list -deps` resolves the
# full transitive graph (tidy populates it; fall back to download).
if [ -f go.mod ] && command -v go >/dev/null 2>&1; then
  log "go mod tidy"
  GOFLAGS="-mod=mod" go mod tidy 2>/dev/null || GOFLAGS="-mod=mod" go mod download 2>/dev/null
fi

# Ruby — a Gemfile.lock makes resolution deterministic.
if [ -f Gemfile ] && [ ! -f Gemfile.lock ] && command -v bundle >/dev/null 2>&1; then
  log "bundle lock"
  bundle lock 2>/dev/null || bundle install 2>/dev/null
fi

# Gradle (java-gradle / Android) — resolve so cdxgen sees the full graph. For
# Android the image's ANDROID_HOME enables AGP.
if { [ -f build.gradle ] || [ -f build.gradle.kts ]; } && command -v gradle >/dev/null 2>&1; then
  log "gradle dependencies"
  if [ -x ./gradlew ]; then
    ./gradlew --no-daemon :app:dependencies >/dev/null 2>&1 \
      || ./gradlew --no-daemon dependencies >/dev/null 2>&1 || true
  else
    gradle --no-daemon :app:dependencies >/dev/null 2>&1 \
      || gradle --no-daemon dependencies >/dev/null 2>&1 || true
  fi
fi

# Python — install into the image's env so transitive deps surface (cdxgen also
# auto-installs, but an explicit pass is a safety net for requirements.txt).
if [ -f requirements.txt ] && command -v pip3 >/dev/null 2>&1; then
  log "pip install requirements"
  pip3 install -q -r requirements.txt 2>/dev/null \
    || pip3 install -q --break-system-packages -r requirements.txt 2>/dev/null
fi

# .NET — restore so obj/project.assets.json carries transitive NuGet deps.
if { ls ./*.csproj >/dev/null 2>&1 || ls ./*.sln >/dev/null 2>&1; } && command -v dotnet >/dev/null 2>&1; then
  log "dotnet restore"
  dotnet restore >/dev/null 2>&1 || true
fi

# Swift / SPM — resolve generates Package.resolved so cdxgen sees the graph.
# Skipped when Package.resolved is already committed: the committed lockfile
# IS the resolved truth, and re-resolving hits the network and can drag in
# unrelated tooling / "unspecified" versions (BomLens build-prep parity).
if [ -f Package.swift ] && [ ! -f Package.resolved ] && command -v swift >/dev/null 2>&1; then
  log "swift package resolve"
  swift package resolve >/dev/null 2>&1 || true
fi

# Maven (pom.xml) / PHP (composer) / Node (package.json) — no pre-resolve here:
# cdxgen invokes maven / composer / its node analyzer directly and resolves the
# transitive graph itself (a separate step was redundant + noisy, per BomLens).

# --- locate cdxgen (path differs per image) and generate the SBOM ---
# Phase L: with a Podfile present and no `pod` CLI, cdxgen's cocoapods
# cataloger throws (TypeError on undefined stdout) and kills the whole run —
# exclude the type; the worker fills pods back in from Podfile.lock
# (integrations/cocoapods_lockfile.py). The in-process adapter carries the
# same guard in integrations/cdxgen.py (_podfile_present) — keep both in sync.
# Depth-bounded find mirrors the adapter's depth-3 glob; Pods/ copies ignored.
set -- -r --no-validate --spec-version "$SPEC" -o "$OUT"
if find "$SRC" -maxdepth 3 -name Podfile -type f -not -path '*/Pods/*' 2>/dev/null | grep -q .; then
  log "Podfile detected — cdxgen --exclude-type cocoapods (pod CLI absent in image)"
  set -- "$@" --exclude-type cocoapods
fi
if command -v cdxgen >/dev/null 2>&1; then
  log "cdxgen (PATH)"
  cdxgen "$@" "$SRC"
elif [ -f /opt/cdxgen/bin/cdxgen.js ]; then
  log "cdxgen (/opt/cdxgen/bin/cdxgen.js)"
  node /opt/cdxgen/bin/cdxgen.js "$@" "$SRC"
elif [ -f /opt/bin/cdxgen ]; then
  log "cdxgen (/opt/bin/cdxgen)"
  /opt/bin/cdxgen "$@" "$SRC"
else
  echo "[build-prep] ERROR: cdxgen not found in image" >&2
  exit 1
fi
