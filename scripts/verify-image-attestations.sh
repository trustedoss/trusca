#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TRUSCA - assert a multi-arch image still carries its build attestations (ER18).
#
# Usage:
#   scripts/verify-image-attestations.sh <image-ref> <expected-platform-count>
#
# Why this exists
# ---------------
# The release workflow builds each architecture on its own runner, pushes it by
# digest, and stitches the digests into a manifest list with
# `docker buildx imagetools create`. With attestations enabled, a push-by-digest
# push is not a plain manifest: buildx pushes an OCI image INDEX holding the
# platform manifest plus an attestation manifest linked to it through the
# `vnd.docker.reference.digest` annotation.
#
# `docker manifest create` cannot consume an index and drops the attestations.
# `docker buildx imagetools create` copies every child manifest, so the merged
# list keeps them. That difference is a property of the buildx version, not of
# our YAML, so it is asserted rather than assumed. Verified on buildx v0.33.0;
# release.yml holds the version floor.
#
# Two callers share this file on purpose:
#   * release.yml, so a release that lost its attestations fails instead of
#     publishing silently.
#   * attestation-check.yml, which rehearses the same shape against a throwaway
#     local registry, so a buildx regression surfaces before a tag is cut.
#
# On success the merged index digest is printed and, when running under Actions,
# appended to $GITHUB_OUTPUT as `digest=`. That digest is what the GitHub build
# provenance attestation binds to.
set -euo pipefail

REF="${1:?usage: verify-image-attestations.sh <image-ref> <expected-platform-count>}"
EXPECTED_PLATFORMS="${2:?usage: verify-image-attestations.sh <image-ref> <expected-platform-count>}"

echo "verifying ${REF} (expecting ${EXPECTED_PLATFORMS} platforms)"
docker buildx imagetools inspect "$REF"

raw="$(docker buildx imagetools inspect --raw "$REF")"

# An attestation manifest is marked by the vnd.docker.reference.type annotation
# and carries platform unknown/unknown; everything else is a real platform.
ATT_FILTER='.annotations["vnd.docker.reference.type"] == "attestation-manifest"'

platforms="$(jq "[.manifests[] | select((${ATT_FILTER}) | not)] | length" <<< "$raw")"
attestations="$(jq "[.manifests[] | select(${ATT_FILTER})] | length" <<< "$raw")"
echo "platform manifests=${platforms} attestation manifests=${attestations}"

if [ "$platforms" -ne "$EXPECTED_PLATFORMS" ]; then
  echo "::error::expected ${EXPECTED_PLATFORMS} platform manifests, got ${platforms}"
  exit 1
fi

if [ "$attestations" -ne "$platforms" ]; then
  echo "::error::expected one attestation manifest per platform (${platforms}), got ${attestations}"
  echo "::error::the merge dropped attestations. Check the buildx version and that"
  echo "::error::the merge uses 'docker buildx imagetools create', not 'docker manifest create'."
  exit 1
fi

# Every attestation must point at a platform manifest that is actually in this
# list. A dangling reference means the merge rewrote digests.
dangling="$(jq "
  [ .manifests[] | select(${ATT_FILTER})
    | .annotations[\"vnd.docker.reference.digest\"] ] as \$refs
  | [ .manifests[] | select((${ATT_FILTER}) | not) | .digest ] as \$plat
  | [ \$refs[] | select(. as \$r | \$plat | index(\$r) | not) ] | length
" <<< "$raw")"
if [ "$dangling" -ne 0 ]; then
  echo "::error::${dangling} attestation manifest(s) reference a digest absent from the merged list"
  exit 1
fi

# The manifests existing is not the same as the payloads being readable. Decode
# both signals for every platform.
prov_count="$(docker buildx imagetools inspect "$REF" --format '{{ json .Provenance }}' | jq 'keys | length')"
sbom_count="$(docker buildx imagetools inspect "$REF" --format '{{ json .SBOM }}' | jq 'keys | length')"
echo "provenance platforms=${prov_count} sbom platforms=${sbom_count}"
if [ "$prov_count" -ne "$platforms" ] || [ "$sbom_count" -ne "$platforms" ]; then
  echo "::error::provenance/SBOM did not decode for every platform"
  exit 1
fi

# Read the index digest back from the registry rather than reusing a build
# output, so the attested subject is what a user pulling this ref receives.
digest="$(docker buildx imagetools inspect "$REF" --format '{{ json .Manifest }}' | jq -r '.digest')"
case "$digest" in
  sha256:*) : ;;
  *) echo "::error::could not read the merged index digest (got '${digest}')"; exit 1 ;;
esac

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "digest=${digest}" >> "$GITHUB_OUTPUT"
fi
echo "${digest} carries provenance + SBOM for ${platforms} platforms"
