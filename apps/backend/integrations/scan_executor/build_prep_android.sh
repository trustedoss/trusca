#!/bin/sh
# build_prep_android.sh — runs INSIDE an sbom-scanner-android-sdk<API> sidecar.
#
#   Usage: sh build_prep_android.sh <SRC_DIR> <OUTPUT_FILE> [SPEC_VERSION]
#
# Adapted from BomLens docker/lib/build-prep.sh (Apache-2.0), Android path only.
# The image's ANDROID_HOME lets the Android Gradle Plugin resolve the dependency
# graph; we pre-resolve with `gradle :app:dependencies` so cdxgen's own gradle
# invocation reads a populated cache, then run cdxgen (path differs per image:
# PATH / /opt/cdxgen/bin/cdxgen.js / /opt/bin/cdxgen).
#
# Passed to the sidecar inline via `sh -c "<this script>" sh <src> <out> <spec>`,
# so it never needs to be bind-mounted (the workspace is a named volume the host
# daemon cannot resolve from the worker's filesystem path).
#
# POSIX sh. Best-effort: prep never fails the scan; an empty SBOM is a degraded
# (not fatal) outcome, surfaced as 0 components downstream.
set +e

SRC="${1:-/app}"
OUT="${2:-$SRC/bom.json}"
SPEC="${3:-1.5}"
mkdir -p "${HOME:-/tmp/sbomhome}" 2>/dev/null || true
cd "$SRC" 2>/dev/null || exit 0

if { [ -f build.gradle ] || [ -f build.gradle.kts ]; } && command -v gradle >/dev/null 2>&1; then
  echo "[build-prep] gradle dependencies"
  if [ -x ./gradlew ]; then
    ./gradlew --no-daemon :app:dependencies >/dev/null 2>&1 \
      || ./gradlew --no-daemon dependencies >/dev/null 2>&1 || true
  else
    gradle --no-daemon :app:dependencies >/dev/null 2>&1 \
      || gradle --no-daemon dependencies >/dev/null 2>&1 || true
  fi
fi

if command -v cdxgen >/dev/null 2>&1; then
  echo "[build-prep] cdxgen (PATH)"
  cdxgen -r --no-validate --spec-version "$SPEC" -o "$OUT" "$SRC"
elif [ -f /opt/cdxgen/bin/cdxgen.js ]; then
  echo "[build-prep] cdxgen (/opt/cdxgen/bin/cdxgen.js)"
  node /opt/cdxgen/bin/cdxgen.js -r --no-validate --spec-version "$SPEC" -o "$OUT" "$SRC"
elif [ -f /opt/bin/cdxgen ]; then
  echo "[build-prep] cdxgen (/opt/bin/cdxgen)"
  /opt/bin/cdxgen -r --no-validate --spec-version "$SPEC" -o "$OUT" "$SRC"
else
  echo "[build-prep] ERROR: cdxgen not found in image" >&2
  exit 1
fi
