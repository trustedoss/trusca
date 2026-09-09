#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TrustedOSS Portal - shared docker-compose file-selection helper.
#
# Exposes ``compose_args_from_env`` which sets the caller's ``COMPOSE_ARGS``
# array to the ``-f`` flags a deployment's overlay actually needs.
#
# Usage:
#   source "$ROOT_DIR/scripts/lib/compose_args.sh"
#   compose_args_from_env
#   docker-compose "${COMPOSE_ARGS[@]}" up -d
#
# Why this exists:
#   scripts/upgrade.sh used to hard-code `-f docker-compose.yml` on every
#   docker-compose call, which silently DROPS whatever overlay a deployment
#   actually runs with. An explicit `-f` also overrides the standard
#   COMPOSE_FILE variable, so declaring the overlay the documented way (in
#   .env) had no effect there either. That was fixed in upgrade.sh directly;
#   this extracts the same logic so deploy/hetzner/remote-deploy.sh's own
#   docker-compose calls (the crash-recovery restart, and the demo reseed)
#   can share it instead of re-deriving it a third time by hand (#441 - the
#   reseed branch had its own ad-hoc, plain-string version, and the
#   crash-recovery restart had no COMPOSE_FILE handling at all).
#
#   Concretely: the demo host runs `docker-compose.yml` +
#   `docker-compose.demo.yml`, and the overlay is what passes
#   DEMO_READ_ONLY into the backend and caps the worker at the box's 2 CPUs.
#   A docker-compose call that drops the overlay rebuilds the stack WITHOUT
#   the public read-only lock and with the 4.0 CPU default - a deploy or a
#   crash-recovery restart quietly turning off a safety boundary.
#
# COMPOSE_FILE is read from .env when the environment does not already carry
# it, because that is where an operator declares it and where docker-compose
# itself looks. Unset (the single-file default) keeps the previous
# behaviour. Must be called from the directory holding docker-compose.yml
# and .env (every caller already `cd`s there first).

compose_args_from_env() {
  if [ -z "${COMPOSE_FILE:-}" ] && [ -f .env ]; then
    COMPOSE_FILE="$(grep -E '^COMPOSE_FILE=' .env | tail -1 | cut -d= -f2- || true)"
    if [ -n "$COMPOSE_FILE" ]; then
      export COMPOSE_FILE
    fi
  fi
  COMPOSE_ARGS=(-f docker-compose.yml)
  if [ -n "${COMPOSE_FILE:-}" ]; then
    COMPOSE_ARGS=()
    local _f _compose_files
    IFS=':' read -ra _compose_files <<< "$COMPOSE_FILE"
    for _f in "${_compose_files[@]}"; do
      [ -n "$_f" ] && COMPOSE_ARGS+=(-f "$_f")
    done
    if [ ${#COMPOSE_ARGS[@]} -eq 0 ]; then
      COMPOSE_ARGS=(-f docker-compose.yml)
    fi
  fi
  # Explicit, unconditional success: the two guards above are `if` blocks
  # whose CONDITION can be false with no `else`, and under `set -e` (every
  # caller sets it) a function's return status is its LAST executed
  # command's - a false `if` condition with no `else` still exits 0 for the
  # `if` construct itself, but this makes it impossible for a future edit to
  # accidentally leave a bare `[ cond ] && action` as the final statement
  # here, which WOULD make this function's return status (and therefore
  # `compose_args_from_env && next_thing` at every call site) fail whenever
  # cond happens to be false. That exact bug shipped once in this file's
  # first version and was only caught by the test suite, not by shellcheck.
  return 0
}
