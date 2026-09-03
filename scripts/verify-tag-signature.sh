#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TRUSCA - check that a release tag carries a valid signature (ER18).
#
# Usage:
#   scripts/verify-tag-signature.sh <tag>
#
# Environment:
#   REQUIRE_SIGNED_TAGS   "true" makes an unsigned or unverifiable tag a hard
#                         failure. Anything else reports the same finding and
#                         exits 0.
#   ALLOWED_SIGNERS_FILE  Path to the allowed-signers file. Defaults to
#                         .github/allowed_signers.
#
# Why the switch exists, and when it goes away
# --------------------------------------------
# The repository decided on SSH tag signing by a single release manager, but
# registering the key and configuring git is the release manager's own action,
# not something CI can do. Until that has happened every tag is unsigned, so a
# check that failed immediately would block releasing rather than improve it.
#
# This is therefore reported but not enforced by default. Once the signing key
# is registered and .github/allowed_signers lists it, set
# REQUIRE_SIGNED_TAGS=true in release.yml and the check becomes a gate. That
# flip is the whole remaining step; nothing else changes.
#
# Verification uses git's own SSH signature support (git >= 2.34), which reads
# the allowed-signers file to decide WHICH keys count. A signature that merely
# verifies cryptographically is worth nothing on its own: it says the tag was
# signed by somebody, not by us. That file is the difference.
set -euo pipefail

TAG="${1:?usage: verify-tag-signature.sh <tag>}"
ALLOWED_SIGNERS_FILE="${ALLOWED_SIGNERS_FILE:-.github/allowed_signers}"
REQUIRE="${REQUIRE_SIGNED_TAGS:-false}"

fail_or_warn() {
  local message="$1"
  if [ "$REQUIRE" = "true" ]; then
    echo "::error::${message}"
    exit 1
  fi
  echo "::warning::${message}"
  echo "REQUIRE_SIGNED_TAGS is not true, so this is reported and not enforced."
  exit 0
}

if ! git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  # A missing tag is a workflow wiring fault, not a signing posture, so it is
  # always an error regardless of the switch.
  echo "::error::tag '${TAG}' does not exist in this checkout (fetch tags first)"
  exit 1
fi

if [ ! -f "$ALLOWED_SIGNERS_FILE" ]; then
  fail_or_warn "no allowed-signers file at ${ALLOWED_SIGNERS_FILE}, so a signature could not be attributed to a known release manager"
fi

git config gpg.ssh.allowedSignersFile "$ALLOWED_SIGNERS_FILE"

# A lightweight tag is a ref pointing straight at a commit; there is no tag
# object to carry a signature, so it can never be signed. Release tags must be
# annotated (`git tag -s`, which implies -a). Reported on its own because
# "cannot verify a non-tag object" reads like a broken signature otherwise.
if [ "$(git cat-file -t "refs/tags/${TAG}")" != "tag" ]; then
  fail_or_warn "tag '${TAG}' is lightweight, so it carries no signature at all; release tags must be annotated and signed (git tag -s)"
fi

if ! output="$(git verify-tag --raw "$TAG" 2>&1)"; then
  case "$output" in
    *"no signature found"*)
      fail_or_warn "tag '${TAG}' is annotated but not signed" ;;
    *)
      echo "$output"
      fail_or_warn "tag '${TAG}' has a signature that does not verify against ${ALLOWED_SIGNERS_FILE}" ;;
  esac
fi

echo "$output"
echo "tag ${TAG} is signed by a key listed in ${ALLOWED_SIGNERS_FILE}"
