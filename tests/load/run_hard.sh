#!/usr/bin/env bash
# Hard load run for the 10k-user profile (headless, strict SLO gate).
#
# Drives the locustfile against a running dev stack. On macOS/Colima the
# loopback inside a container is the VM, not the host, so we attach the locust
# container to the compose network and target the backend by service name.
#
# Usage:
#   tests/load/run_hard.sh [users] [spawn_rate] [runtime]
#   tests/load/run_hard.sh 200 40 90s     # SLO gate (dev stack sustains this)
#   tests/load/run_hard.sh 800 100 120s   # stress / breaking-point probe
#
# Env overrides: LOAD_TEST_EMAIL, LOAD_TEST_PASSWORD, LOAD_MAX_P95_MS,
# LOAD_MAX_P99_MS, LOAD_MAX_FAIL_RATIO, LOAD_NETWORK, LOAD_HOST.
set -u
U=${1:-200}; R=${2:-40}; T=${3:-90s}
NET=${LOAD_NETWORK:-trustedoss-portal_default}
HOST=${LOAD_HOST:-http://backend:8000}
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "=== LOAD RUN u=$U r=$R t=$T host=$HOST net=$NET ==="
docker run --rm --network "$NET" \
  -v "$HERE:/mnt/locust:ro" \
  -e LOAD_TEST_EMAIL="${LOAD_TEST_EMAIL:-e2e-admin@trustedoss.dev}" \
  -e LOAD_TEST_PASSWORD="${LOAD_TEST_PASSWORD:-E2eAdminPass2026}" \
  -e LOAD_MAX_P95_MS="${LOAD_MAX_P95_MS:-1500}" \
  -e LOAD_MAX_P99_MS="${LOAD_MAX_P99_MS:-4000}" \
  -e LOAD_MAX_FAIL_RATIO="${LOAD_MAX_FAIL_RATIO:-0.01}" \
  locustio/locust:2.31.8 \
  -f /mnt/locust/locustfile.py --headless -u "$U" -r "$R" -t "$T" --host "$HOST"
rc=$?
echo "locust_exit=$rc"
exit "$rc"
