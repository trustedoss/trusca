#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TRUSCA - pre-extraction member-path check (#400, security review finding
# #400-S3).
#
# Exposes `verify_bundle_members_are_contained <bundle.tar>`.
#
# Runs `tar -tf` (list only, no filesystem write) BEFORE `tar -xf` and
# rejects the bundle if any member name is absolute or contains a literal
# `..` path segment. This has to run BEFORE extraction, not after: a member
# named e.g. `../../../etc/cron.d/x` that an unprotected `tar` actually wrote
# would land OUTSIDE the extraction directory, so a check that only walks
# the extraction directory afterward can never see it there to catch it,
# tried exactly that first, verified (via a deliberately broken build) that
# it does not actually work.
#
# Both bsdtar and GNU tar already refuse such a member at `-x` time by
# default (confirmed during security review against both), so on those tools
# `tar -xf` would have failed anyway; this check exists for whichever `tar`
# binary the install target happens to have, not something this script
# pins a minimum version for, and it fails with a clear message instead of
# a raw `tar` error.
#
# Deliberately NOT a defence against a symlink-then-write-through-symlink
# escape (a symlink member pointing outside the extraction dir, followed by
# a member that writes through it): detecting that from a member list
# alone (rather than actually extracting and observing where bytes land)
# is a materially bigger piece of tar-header parsing than this feature
# warrants, and both tar implementations tested during security review
# already refuse to extract that shape by default.
verify_bundle_members_are_contained() {
  local bundle="$1"
  local member
  while IFS= read -r member; do
    case "$member" in
      /*)
        printf 'bundle member has an absolute path: %s\n' "$member" >&2
        return 1
        ;;
    esac
    case "/$member/" in
      */../*)
        printf "bundle member contains a '..' path segment: %s\n" "$member" >&2
        return 1
        ;;
    esac
  done < <(tar -tf "$bundle")
  return 0
}
