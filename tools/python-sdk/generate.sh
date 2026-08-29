#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
#
# generate.sh: build the Python client SDK from the committed OpenAPI spec
# (C5), package it, and leave a wheel + sdist in dist/.
#
# NOT committed to the repository. openapi-generator produces ~900 files for
# this API's surface; vendoring that much generated code would make every
# API-shape change a multi-thousand-line diff noise in review, on top of the
# repo-size cost. Instead this mirrors how the release SBOM is handled
# (.github/workflows/release.yml): generated fresh at release-tag time from
# the pinned spec, then attached to the GitHub Release as a downloadable
# asset. Anyone can also run this locally against any commit's spec.
#
# Usage:
#   bash tools/python-sdk/generate.sh [version]
#
#   version   PEP 440 version string for the built package. Defaults to
#             0.0.0.dev0 (a local trial build, never what a release ships).
#
# Requires: npm (this dir's own package.json pins the generator), a JVM
# (the generator itself runs on one; this is a build-time-only dependency
# that never runs in the deployed product), and Python's `build` package
# (`pip install build`) for the final packaging step.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
SPEC="$REPO_ROOT/docs-site/static/openapi.json"
OUT="$HERE/build"
VERSION="${1:-0.0.0.dev0}"

[ -f "$SPEC" ] || {
  echo "missing $SPEC, run 'python scripts/dump_openapi.py' first" >&2
  exit 1
}

echo "==> installing generator tooling (pinned in tools/python-sdk/package.json)"
cd "$HERE"
npm ci

echo "==> generating trusca_client $VERSION from $SPEC"
rm -rf "$OUT"
npx --no-install openapi-generator-cli generate \
  -i "$SPEC" \
  -g python \
  -o "$OUT" \
  --package-name trusca_client \
  --additional-properties="packageVersion=${VERSION},packageUrl=https://github.com/trustedoss/trusca"

echo "==> replacing the generator's boilerplate README + pyproject metadata"
cp "$HERE/README.md.template" "$OUT/README.md"
python3 "$HERE/patch_pyproject.py" "$OUT/pyproject.toml"
# setup.py duplicates pyproject.toml's [project] metadata for tooling that
# predates PEP 621, but setuptools' build backend runs it INSTEAD of just
# reading pyproject.toml when both are present, silently reverting every
# patch above (author, license, URLs) back to the generator's placeholders.
# `python -m build` needs only pyproject.toml.
rm -f "$OUT/setup.py"

echo "==> building wheel + sdist"
python3 -m pip install --quiet --upgrade build
python3 -m build --outdir "$HERE/dist" "$OUT"

echo "==> done:"
find "$HERE/dist" -maxdepth 1 -name "*${VERSION}*" -print
