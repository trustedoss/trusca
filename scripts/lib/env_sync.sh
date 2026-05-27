#!/usr/bin/env bash
# TrustedOSS Portal — shared .env sync helper (W6-chore-seed B).
#
# Exposes ``env_append_only_sync <example_file> <target_env_file>``.
#
# Appends every uncommented ``KEY=...`` line from ``example_file`` whose KEY
# does not already exist (commented OR uncommented) in ``target_env_file``.
# NEVER overwrites an existing value, NEVER comments out an existing line,
# NEVER reorders existing lines.
#
# Behaviour:
#   - If ``target_env_file`` does not exist, this is a no-op (the caller
#     bootstraps a fresh .env elsewhere — see install.sh's ``cp .env.example
#     .env`` path). The helper is a *sync* primitive, not a bootstrap one.
#   - Empty lines and lines starting with ``#`` in the example file are
#     copied through ONLY when they immediately precede a missing-key line,
#     so the operator's .env stays browsable with the same section comments
#     the example carries.
#   - The comparison is per-KEY only — the value is irrelevant. A KEY that
#     exists in the target with ANY value (including the empty string) is
#     considered present.
#
# Usage:
#   source "$ROOT_DIR/scripts/lib/env_sync.sh"
#   env_append_only_sync .env.example .env
#
# Why this exists:
#   Pre-W6-chore-seed, install.sh wrote a fresh .env from .env.example only
#   on first install; upgrade.sh and dev-reset.sh never touched .env. When a
#   new release shipped a new env knob (e.g. ``VULN_REMATCH_INTERVAL_HOURS``
#   in W6-#42), the operator's existing .env stayed silently stale and the
#   feature ran on the binary's hard-coded defaults with no operator-visible
#   trace. This helper closes that gap without touching what the operator
#   already configured.

set -euo pipefail

# Extract the set of KEY names defined in a .env file. A "definition" is any
# line matching ``KEY=...`` after optional leading whitespace. We accept both
# ``KEY=`` and ``# KEY=`` so a commented-out line still counts as "the operator
# knows about this knob" — re-appending would be noise.
_env_sync_extract_keys() {
  local file="$1"
  # ``KEY = ...`` is NOT a valid env line; ``=`` with no key prefix is also
  # ignored. We anchor to lines whose first token (after optional ``#`` +
  # optional whitespace) is an identifier followed by ``=``.
  grep -E '^[[:space:]]*#?[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$file" \
    | sed -E 's/^[[:space:]]*#?[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=.*/\1/' \
    | sort -u
}

env_append_only_sync() {
  local example_file="$1"
  local target_file="$2"

  if [[ ! -f "$example_file" ]]; then
    return 0
  fi
  if [[ ! -f "$target_file" ]]; then
    # No target → nothing to sync into. The caller bootstraps .env elsewhere;
    # this helper only adds missing keys to an existing operator file.
    return 0
  fi

  local existing_keys
  existing_keys="$(_env_sync_extract_keys "$target_file" || true)"

  # Build a temp file with the lines we are about to append. We walk the
  # example file once, deciding for each line whether to carry it through:
  #
  #   * blank line → buffer it (might precede a new section header)
  #   * comment line → buffer it (might be the section header for new keys)
  #   * KEY=... line whose KEY is already in target → discard buffered comments
  #   * KEY=... line whose KEY is NOT in target → flush buffer + emit the line
  #
  # That keeps section comments grouped with the new keys they describe, so
  # the operator's freshly-synced .env reads like the example.
  local appended_count=0
  local pending_buffer=""
  local pending_header_emitted=0

  while IFS='' read -r line || [[ -n "$line" ]]; do
    if [[ -z "${line//[[:space:]]/}" ]]; then
      # blank line — buffer
      pending_buffer+=$'\n'"$line"
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*# ]]; then
      # comment line — buffer (carry section headers through with the keys)
      pending_buffer+=$'\n'"$line"
      continue
    fi
    if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)= ]]; then
      local key="${BASH_REMATCH[1]}"
      if grep -qxF "$key" <<<"$existing_keys"; then
        # already known to target — discard buffered comments to avoid drift
        pending_buffer=""
        continue
      fi
      # missing key — flush a one-time header separator the first time, then
      # the buffered comments + the line itself.
      if (( pending_header_emitted == 0 )); then
        printf '\n# ---- appended by env_sync from %s ----\n' "$example_file"
        pending_header_emitted=1
      fi
      if [[ -n "$pending_buffer" ]]; then
        printf '%s\n' "${pending_buffer#$'\n'}"
        pending_buffer=""
      fi
      printf '%s\n' "$line"
      appended_count=$((appended_count + 1))
      continue
    fi
    # Anything else (export FOO, etc.) — ignore for sync purposes.
  done <"$example_file" >>"$target_file"

  if (( appended_count > 0 )); then
    echo "  env_sync: appended ${appended_count} new key(s) from $example_file → $target_file"
  fi
}
