#!/usr/bin/env bash
# Remote half of the demo-host maintenance workflow
# (.github/workflows/maintain-demo-host.yml).
#
# Runs ON THE DEMO SERVER, fed over SSH stdin by the workflow:
#
#   ssh user@host "ACTION='report' REMOTE_PATH='/opt/trustedoss/portal' bash -s" \
#       < deploy/hetzner/remote-maintain.sh
#
# Why this exists: a deploy that fills the disk fails while pulling image layers
# and can leave the stack partially down, and the only recovery path was a human
# with an SSH session. The deploy workflow already holds a working SSH channel to
# this host, so the same channel answers "how full is it" and "reclaim what is
# not in use" — as committed commands, reviewable before they run and logged
# after.
#
# Inputs (environment, set by the workflow on the ssh command line):
#   ACTION       required — report | prune | restart. Validated here as well as
#                in the workflow: this script is the thing that runs as root's
#                neighbour on a live host, so it does not trust its caller.
#   REMOTE_PATH  optional — repo checkout on the server (default
#                /opt/trustedoss/portal).
#
# CLAUDE.md compliance:
#   - core rule #10: docker-compose (V1 invocation). The server's .env carries
#     COMPOSE_FILE so the demo overlay applies without -f.

set -euo pipefail

ACTION="${ACTION:?ACTION env var is required (set by the maintenance workflow)}"
REMOTE_PATH="${REMOTE_PATH:-/opt/trustedoss/portal}"

case "$ACTION" in
  report | prune | restart) : ;;
  *)
    echo "unknown ACTION '$ACTION' (expected report | prune | restart)" >&2
    exit 2
    ;;
esac

cd "$REMOTE_PATH"

# ---------------------------------------------------------------------------
# Always report first. A prune that reclaims nothing and a prune that reclaims
# 30 GB look identical in the workflow's exit code, so the before/after numbers
# are the actual output of this script.
# ---------------------------------------------------------------------------

report() {
  echo "==> filesystem"
  df -h / /var/lib/docker /var/lib/containerd 2>/dev/null | sort -u || df -h /

  echo
  echo "==> docker usage"
  docker system df || true

  echo
  echo "==> compose services"
  # Not `docker-compose ps -q`: the point is to see which services are missing,
  # and a stopped service prints its state here.
  docker-compose ps || true
}

echo "action: $ACTION"
echo "path:   $REMOTE_PATH"
echo

echo "### before"
report

case "$ACTION" in
  report)
    ;;

  prune)
    echo
    echo "==> reclaiming unused images and build cache"
    # -a removes every image no CONTAINER references. The running stack's images
    # are referenced by its containers, so they survive; what goes is the
    # previous release's layers and whatever a failed pull left behind.
    docker image prune -a -f || true
    docker builder prune -a -f || true
    # Dangling volumes are NOT pruned. The stack's data (postgres, workspace,
    # the Trivy DB) lives in volumes, and a mistake there is unrecoverable
    # where a removed image is merely re-pulled.
    echo "==> volumes left untouched by design"
    ;;

  restart)
    echo
    echo "==> bringing any stopped service back up"
    docker-compose up -d
    ;;
esac

if [ "$ACTION" != "report" ]; then
  echo
  echo "### after"
  report
fi
