#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 TRUSCA contributors
# TRUSCA - seed a portal with real rows, then prove they survived an upgrade.
#
# Usage:
#   scripts/ci/upgrade-smoke.sh seed   <base-url> <state-file>
#   scripts/ci/upgrade-smoke.sh verify <base-url> <state-file>
#
# Environment: ADMIN_EMAIL, ADMIN_PASSWORD (the bootstrapped super admin).
#
# Why a script rather than workflow steps
# ---------------------------------------
# upgrade-uat.yml runs `seed` against the PREVIOUS release's images and
# `verify` against the CURRENT ones, so the two halves have to agree exactly on
# what was created. Keeping them in one file makes that agreement checkable.
#
# The calls here deliberately mirror the smoke in install-uat.yml (same
# endpoints, same payload shapes, same poll budget). They are duplicated rather
# than shared because that workflow is being edited elsewhere; once it settles,
# its inline steps can be replaced by `seed` and the duplication collapses.
#
# `seed` is deliberately the WRITE half and `verify` the READ half. What makes
# this an upgrade test rather than another smoke test is that the rows read
# back in `verify` were written by a different, older version of the code.
set -euo pipefail

COMMAND="${1:?usage: upgrade-smoke.sh <seed|verify> <base-url> <state-file>}"
BASE_URL="${2:?usage: upgrade-smoke.sh <seed|verify> <base-url> <state-file>}"
STATE_FILE="${3:?usage: upgrade-smoke.sh <seed|verify> <base-url> <state-file>}"

: "${ADMIN_EMAIL:?ADMIN_EMAIL must be set}"
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD must be set}"

# The scan below really clones this URL. TRUSTEDOSS_SCAN_BACKEND=mock stubs the
# scanner toolchain (cdxgen, scancode, Trivy) but NOT the fetch step, so a
# placeholder URL fails with "could not read Username for 'https://github.com'"
# rather than being skipped. This is the same public fixture repository
# install-uat.yml scans.
SMOKE_GIT_URL="${SMOKE_GIT_URL:-https://github.com/trustedoss-e2e/install-uat-smoke.git}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# The auth router mounts at /auth, not /v1/auth; only the domain routers carry
# the /v1 prefix.
login() {
  local body
  body="$(curl -fsS -X POST "${BASE_URL}/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}")"
  # Fetch and parse as separate statements: piping curl into an interpreter
  # trips the semgrep gha-curl-pipe-shell rule.
  printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}

# POST with an explicit status check so a 4xx body is visible in the log rather
# than collapsed into a curl exit code.
post_json() {
  local path="$1" payload="$2" want="$3" out="$4"
  local status
  status="$(curl -sS -o "$out" -w '%{http_code}' \
    -X POST "${BASE_URL}${path}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$payload")"
  if [ "$status" != "$want" ]; then
    echo "::error::POST ${path} returned HTTP ${status} (expected ${want})"
    cat "$out"
    return 1
  fi
}

json_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$1" "$2"
}

seed() {
  TOKEN="$(login)"
  echo "::add-mask::${TOKEN}"
  echo "logged in as ${ADMIN_EMAIL}"

  post_json /v1/admin/teams \
    '{"name":"Upgrade UAT Team","slug":"upgrade-uat-team","description":"Created by the upgrade-uat seed"}' \
    201 "${WORK_DIR}/team.json"
  local team_id
  team_id="$(json_field "${WORK_DIR}/team.json" id)"
  echo "created team ${team_id}"

  post_json /v1/projects \
    "{\"team_id\":\"${team_id}\",\"name\":\"Upgrade UAT Project\",\"slug\":\"upgrade-uat-project\",\"git_url\":\"${SMOKE_GIT_URL}\"}" \
    201 "${WORK_DIR}/project.json"
  local project_id
  project_id="$(json_field "${WORK_DIR}/project.json" id)"
  echo "created project ${project_id}"

  post_json "/v1/projects/${project_id}/scans" '{"kind":"source"}' 202 "${WORK_DIR}/scan.json"
  local scan_id
  scan_id="$(json_field "${WORK_DIR}/scan.json" id)"
  echo "triggered scan ${scan_id}"

  # TRUSTEDOSS_SCAN_BACKEND=mock short-circuits the pipeline to fixture frames,
  # so this completes in seconds; the real toolchain takes 5-60 minutes.
  local i status
  for i in $(seq 1 30); do
    curl -fsS "${BASE_URL}/v1/scans/${scan_id}" \
      -H "Authorization: Bearer ${TOKEN}" -o "${WORK_DIR}/poll.json"
    status="$(json_field "${WORK_DIR}/poll.json" status)"
    case "$status" in
      succeeded) echo "scan ${scan_id} succeeded after ~$(( (i - 1) * 5 ))s"; break ;;
      failed|cancelled)
        echo "::error::scan ${scan_id} ended '${status}' instead of 'succeeded'"
        cat "${WORK_DIR}/poll.json"; return 1 ;;
      queued|running) echo "waiting for scan (${i}/30, status=${status})"; sleep 5 ;;
      *) echo "::error::unexpected scan status '${status}'"; cat "${WORK_DIR}/poll.json"; return 1 ;;
    esac
  done
  if [ "$status" != "succeeded" ]; then
    echo "::error::scan ${scan_id} never reached a terminal state within the poll budget"
    return 1
  fi

  # Record what the OLDER version wrote. verify() reads these back through the
  # newer code, so any migration that loses or rewrites them shows up as a
  # mismatch rather than as an empty list that still returns 200.
  python3 - "$STATE_FILE" "$team_id" "$project_id" "$scan_id" <<'PY'
import json, sys
state = {"team_id": sys.argv[2], "project_id": sys.argv[3], "scan_id": sys.argv[4]}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(state, fh, indent=2)
print("wrote state:", json.dumps(state))
PY
}

verify() {
  local team_id project_id scan_id
  team_id="$(json_field "$STATE_FILE" team_id)"
  project_id="$(json_field "$STATE_FILE" project_id)"
  scan_id="$(json_field "$STATE_FILE" scan_id)"

  # The credentials were created by the previous version. A migration that
  # touched users or password hashes fails right here.
  TOKEN="$(login)"
  echo "::add-mask::${TOKEN}"
  echo "logged in after upgrade with pre-upgrade credentials"

  # The project must come back by its ORIGINAL id.
  local status
  status="$(curl -sS -o "${WORK_DIR}/project.json" -w '%{http_code}' \
    "${BASE_URL}/v1/projects/${project_id}" -H "Authorization: Bearer ${TOKEN}")"
  if [ "$status" != "200" ]; then
    echo "::error::pre-upgrade project ${project_id} returned HTTP ${status} after the upgrade"
    cat "${WORK_DIR}/project.json"
    return 1
  fi
  local got_team
  got_team="$(json_field "${WORK_DIR}/project.json" team_id)"
  if [ "$got_team" != "$team_id" ]; then
    echo "::error::project ${project_id} now reports team ${got_team}, was ${team_id}"
    return 1
  fi
  echo "project ${project_id} survived, still on team ${team_id}"

  # The scan and its results must survive too, still terminal.
  status="$(curl -sS -o "${WORK_DIR}/scan.json" -w '%{http_code}' \
    "${BASE_URL}/v1/scans/${scan_id}" -H "Authorization: Bearer ${TOKEN}")"
  if [ "$status" != "200" ]; then
    echo "::error::pre-upgrade scan ${scan_id} returned HTTP ${status} after the upgrade"
    cat "${WORK_DIR}/scan.json"
    return 1
  fi
  local scan_status
  scan_status="$(json_field "${WORK_DIR}/scan.json" status)"
  if [ "$scan_status" != "succeeded" ]; then
    echo "::error::scan ${scan_id} reads '${scan_status}' after the upgrade, was 'succeeded'"
    cat "${WORK_DIR}/scan.json"
    return 1
  fi
  echo "scan ${scan_id} still reads succeeded"

  # Reading the derived rows exercises the tables a data migration is most
  # likely to touch. A 500 here is the signal; an empty list is not, because
  # the mock fixture's component count is not this script's contract.
  for path in "/v1/projects/${project_id}/components" "/v1/projects/${project_id}/vulnerabilities"; do
    status="$(curl -sS -o "${WORK_DIR}/derived.json" -w '%{http_code}' \
      "${BASE_URL}${path}" -H "Authorization: Bearer ${TOKEN}")"
    if [ "$status" != "200" ]; then
      echo "::error::GET ${path} returned HTTP ${status} against the migrated schema"
      cat "${WORK_DIR}/derived.json"
      return 1
    fi
    echo "GET ${path} -> 200"
  done

  # Writes must work on the migrated schema, not just reads. A NOT NULL column
  # added without a default breaks here and nowhere above.
  post_json /v1/projects \
    "{\"team_id\":\"${team_id}\",\"name\":\"Post Upgrade Project\",\"slug\":\"post-upgrade-project\",\"git_url\":\"${SMOKE_GIT_URL}\"}" \
    201 "${WORK_DIR}/new-project.json"
  echo "created a new project on the migrated schema"
}

case "$COMMAND" in
  seed)   seed ;;
  verify) verify ;;
  *) echo "::error::unknown command '${COMMAND}' (expected seed or verify)"; exit 1 ;;
esac
