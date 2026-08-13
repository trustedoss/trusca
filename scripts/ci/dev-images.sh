#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# Content-addressed dev images for CI.
#
# WHY THIS EXISTS: six workflows bring up the dev compose stack, and each one
# built the same three images from scratch. On a cold runner that is about
# seven minutes of the ten-minute ui-gates job, repeated per workflow, and it
# is also seven minutes of exposure to apt / pip / npm / GitHub mirrors — a
# cosign download failing with curl 56 has already killed a run.
#
# The dev compose file bind-mounts the source into every container
# (`./apps/backend:/app`, and the frontend likewise for HMR), so the code baked
# into these images at build time is never what runs. What the image actually
# contributes is the installed toolchain: apt packages, pip wheels, npm
# modules. That is why an image can be reused across commits, and why the tag
# is a hash of the files that decide the toolchain rather than of the tree.
#
# Change any of those files and the key changes, so no stale image can be
# served for a build that would now produce something different. Change only
# application code and the key holds, which is the common case.
#
# The images live in ghcr rather than the Actions cache on purpose: the Actions
# cache is a single 10 GB budget per repository, shared with the Playwright
# browsers and npm caches. Pushing multi-gigabyte image layers into it would
# evict the caches that make the other jobs fast.
#
# Usage:
#   dev-images.sh key <service>            print the content key for one service
#   dev-images.sh ensure <service>...      pull each image, build what is missing
#   dev-images.sh publish <service>...     build and push (main only)
#
# Services are compose service names: backend, celery-worker, frontend.

set -euo pipefail

REGISTRY="${DEV_IMAGE_REGISTRY:-ghcr.io/trustedoss}"
COMPOSE_FILE="${DEV_IMAGE_COMPOSE_FILE:-docker-compose.dev.yml}"

# Compose V1 where the caller installed it (docs-uat, verify-specs), V2
# otherwise. CLAUDE.md rule #10 is about the development environment, which has
# no V2; on a runner both exist and the workflows already differ.
compose() {
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "$COMPOSE_FILE" "$@"
  else
    docker compose -f "$COMPOSE_FILE" "$@"
  fi
}

# The files that decide what a service's image contains. Anything that changes
# an installed package, a pinned tool version or a build step belongs here.
key_inputs() {
  case "$1" in
    backend)
      echo "apps/backend/Dockerfile apps/backend/requirements.txt apps/backend/requirements-dev.txt"
      ;;
    celery-worker|celery-beat)
      echo "apps/backend/Dockerfile.worker apps/backend/requirements.txt apps/backend/requirements-dev.txt"
      ;;
    frontend)
      echo "apps/frontend/Dockerfile apps/frontend/package.json apps/frontend/package-lock.json"
      ;;
    *)
      echo "unknown service: $1" >&2
      return 1
      ;;
  esac
}

# The ghcr package a service's image is published under.
package_name() {
  case "$1" in
    backend) echo "trusca-dev-backend" ;;
    celery-worker|celery-beat) echo "trusca-dev-worker" ;;
    frontend) echo "trusca-dev-frontend" ;;
    *) echo "unknown service: $1" >&2; return 1 ;;
  esac
}

# The tag docker-compose.dev.yml expects to find locally.
local_tag() {
  case "$1" in
    backend) echo "trustedoss/backend:dev" ;;
    celery-worker|celery-beat) echo "trustedoss/backend-worker:dev" ;;
    frontend) echo "trustedoss/frontend:dev" ;;
    *) echo "unknown service: $1" >&2; return 1 ;;
  esac
}

content_key() {
  local service="$1" files
  read -r -a files <<< "$(key_inputs "$service")"
  # Hash the file contents AND their names, so moving a requirement between
  # files is not invisible. `sort` is not needed — key_inputs is a fixed order.
  {
    printf '%s\n' "${files[@]}"
    cat "${files[@]}"
  } | sha256sum | cut -c1-16
}

remote_ref() {
  local service="$1"
  echo "${REGISTRY}/$(package_name "$service"):$(content_key "$service")"
}

cmd_key() {
  content_key "$1"
}

cmd_ensure() {
  local missing=() service ref
  for service in "$@"; do
    ref="$(remote_ref "$service")"
    echo "::group::dev-images: $service ← $ref"
    if docker pull --quiet "$ref" 2>/dev/null; then
      docker tag "$ref" "$(local_tag "$service")"
      echo "pulled"
    else
      echo "not published for this key — building locally"
      missing+=("$service")
    fi
    echo "::endgroup::"
  done

  if [ ${#missing[@]} -gt 0 ]; then
    echo "::group::dev-images: building ${missing[*]}"
    compose build "${missing[@]}"
    echo "::endgroup::"
  fi
}

cmd_publish() {
  local service ref
  echo "::group::dev-images: building $*"
  compose build "$@"
  echo "::endgroup::"

  for service in "$@"; do
    ref="$(remote_ref "$service")"
    docker tag "$(local_tag "$service")" "$ref"
    echo "pushing $ref"
    docker push "$ref"
  done
}

main() {
  local action="${1:-}"
  shift || true
  case "$action" in
    key)
      [ $# -eq 1 ] || { echo "usage: dev-images.sh key <service>" >&2; exit 2; }
      cmd_key "$1"
      ;;
    ensure)
      [ $# -ge 1 ] || { echo "usage: dev-images.sh ensure <service>..." >&2; exit 2; }
      cmd_ensure "$@"
      ;;
    publish)
      [ $# -ge 1 ] || { echo "usage: dev-images.sh publish <service>..." >&2; exit 2; }
      cmd_publish "$@"
      ;;
    *)
      echo "usage: dev-images.sh {key|ensure|publish} <service>..." >&2
      exit 2
      ;;
  esac
}

main "$@"
