#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TrustedOSS Portal - what the BomLens scan engine needs from the host.
#
# The engine is the default scan engine under docker-compose.yml. It runs on
# worker-engine, which creates the engine's containers through the Docker API,
# and those containers need three things compose cannot give itself:
#
#   1. the names of the project's workspace and Trivy volumes, which depend on
#      the compose project name (SCAN_WORKSPACE_VOLUME, SCAN_TRIVY_CACHE_VOLUME);
#   2. the egress network stage 1 and the image scans reach the registries on,
#      created once and with no traffic between the containers on it;
#   3. for an air-gapped host, the in-process engine: the engine pulls its images
#      from ghcr.io when a scan needs them.
#
# Usage (from the directory holding docker-compose.yml and .env):
#   source "$ROOT_DIR/scripts/lib/engine_env.sh"
#   engine_env_prepare            # install / upgrade on a connected host
#   engine_env_prepare offline    # an --offline install: keep the in-process engine
#
# Never overwrites a value the operator set: every key is written only when it is
# missing or empty. The network is created only when Docker answers and it does not
# exist yet. Output goes through the caller's ok/note helpers when they exist.

_engine_say() {
  if declare -F note >/dev/null 2>&1; then note "$1"; else printf '  %s\n' "$1"; fi
}

# The compose project name as docker-compose derives it: COMPOSE_PROJECT_NAME from
# the environment or .env, else the directory name, lower-cased and reduced to the
# characters a project name may hold.
engine_project_name() {
  local name="${COMPOSE_PROJECT_NAME:-}"
  if [ -z "$name" ] && [ -f .env ]; then
    name="$(grep -E '^COMPOSE_PROJECT_NAME=' .env | tail -1 | cut -d= -f2- || true)"
  fi
  [ -n "$name" ] || name="$(basename "$PWD")"
  printf '%s' "$name" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-'
}

# engine_env_set_if_empty KEY VALUE - append KEY=VALUE to .env, or fill the key when
# it is there with nothing after the "=". A key with a value is left alone.
engine_env_set_if_empty() {
  local key="$1" value="$2"
  [ -f .env ] || : >.env
  if grep -qE "^${key}=.+" .env; then
    return 0
  fi
  if grep -qE "^${key}=" .env; then
    local tmp
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k && $2=="" {print k "=" v; next} {print}' .env >"$tmp"
    cat "$tmp" >.env
    rm -f "$tmp"
  else
    printf '%s=%s\n' "$key" "$value" >>.env
  fi
}

engine_env_prepare() {
  local mode="${1:-}"
  local project
  project="$(engine_project_name)"
  engine_env_set_if_empty SCAN_WORKSPACE_VOLUME "${project}_workspace"
  engine_env_set_if_empty SCAN_TRIVY_CACHE_VOLUME "${project}_trivy-cache"
  if [ "$mode" = "offline" ]; then
    engine_env_set_if_empty SCAN_ENGINE inprocess
    engine_env_set_if_empty SCAN_CONTAINER_ENGINE inprocess
    _engine_say "offline install: scans use the in-process engine (the BomLens engine pulls its images from ghcr.io)"
  elif grep -qE '^TRIVY_DB_BOOTSTRAP_ON_START=false' .env 2>/dev/null; then
    # An air-gapped install that was made before this key existed: it has no
    # SCAN_ENGINE of its own, and following the default would send its scans to an
    # engine that cannot pull its images.
    if ! grep -qE '^SCAN_ENGINE=' .env; then
      engine_env_set_if_empty SCAN_ENGINE inprocess
      engine_env_set_if_empty SCAN_CONTAINER_ENGINE inprocess
      _engine_say "air-gapped install (TRIVY_DB_BOOTSTRAP_ON_START=false): kept the in-process engine; remove SCAN_ENGINE and SCAN_CONTAINER_ENGINE from .env to use the BomLens engine"
    fi
  fi
  local network
  network="$(grep -E '^SCAN_BOMLENS_NETWORK=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  [ -n "$network" ] || network="trustedoss-scan-egress"
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if ! docker network inspect "$network" >/dev/null 2>&1; then
      if docker network create --opt com.docker.network.bridge.enable_icc=false "$network" >/dev/null 2>&1; then
        _engine_say "created the engine's egress network ${network}"
      else
        _engine_say "could not create the engine's egress network ${network}; create it with: docker network create --opt com.docker.network.bridge.enable_icc=false ${network}"
      fi
    fi
  else
    _engine_say "Docker did not answer: create the engine's egress network yourself: docker network create --opt com.docker.network.bridge.enable_icc=false ${network}"
  fi
}
