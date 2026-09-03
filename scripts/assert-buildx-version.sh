#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TRUSCA - fail unless the local buildx is at or above a verified floor (ER18).
#
# Usage:
#   scripts/assert-buildx-version.sh <minimum-version>   # e.g. 0.33.0
#
# A floor, not a pin. Whether attestation manifests survive
# `docker buildx imagetools create` is a property of the buildx build, and
# v0.33.0 is the oldest version we have actually verified it on. Because
# docker/setup-buildx-action installs the latest release, this normally passes
# untouched; it exists so a pinned or rolled-back builder fails loudly here
# instead of silently publishing images with their attestations stripped.
set -euo pipefail

MIN="${1:?usage: assert-buildx-version.sh <minimum-version>}"

# `docker buildx version` prints "github.com/docker/buildx v0.37.0 <sha>".
raw="$(docker buildx version)"
echo "$raw"
got="$(printf '%s' "$raw" | awk '{print $2}' | sed 's/^v//')"

if [ -z "$got" ]; then
  echo "::error::could not parse a buildx version out of: ${raw}"
  exit 1
fi

# sort -V puts the lower version first, so if the floor sorts first (or the two
# are equal) we are at or above it.
lowest="$(printf '%s\n%s\n' "$got" "$MIN" | sort -V | head -n1)"
if [ "$lowest" != "$MIN" ] && [ "$got" != "$MIN" ]; then
  echo "::error::buildx ${got} is below the verified floor ${MIN}."
  echo "::error::Attestations may not survive 'docker buildx imagetools create' on this version."
  exit 1
fi

echo "buildx ${got} is at or above the verified floor ${MIN}"
