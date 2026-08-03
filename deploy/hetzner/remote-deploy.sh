#!/usr/bin/env bash
# Remote half of the Hetzner demo CD pipeline (.github/workflows/deploy-hetzner.yml).
#
# Runs ON THE DEMO SERVER, fed over SSH stdin by the workflow:
#
#   ssh user@host "TAG='vX.Y.Z' REMOTE_PATH='/opt/trustedoss/portal' bash -s" \
#       < deploy/hetzner/remote-deploy.sh
#
# It checks out the requested release tag, pins IMAGE_TAG in .env to match, then
# delegates the actual image pull / recreate / migrate / health-probe to the
# existing scripts/upgrade.sh (single source of truth for the upgrade flow).
#
# Inputs (environment, set by the workflow on the ssh command line):
#   TAG          required — release tag to deploy, ALREADY validated as a strict
#                 ^v[0-9]+\.[0-9]+\.[0-9]+$ semver by the workflow before it
#                 reaches this shell (so the substitutions below are injection-safe).
#   REMOTE_PATH  optional — repo checkout on the server (default /opt/trustedoss/portal).
#
# CLAUDE.md compliance:
#   - core rule #10: docker-compose (V1 invocation) — upgrade.sh enforces it.
#   - core rule  #9: IMAGE_TAG pins an immutable image tag, never :latest.

set -euo pipefail

TAG="${TAG:?TAG env var is required (set by the deploy workflow)}"
REMOTE_PATH="${REMOTE_PATH:-/opt/trustedoss/portal}"

# Defence in depth — re-validate the tag on the server even though the workflow
# already did. A non-semver value here means the call was malformed; refuse
# rather than feed it to git / sed.
if ! printf '%s' "$TAG" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "remote-deploy: TAG '$TAG' is not a vX.Y.Z semver — refusing" >&2
  exit 2
fi

IMG="${TAG#v}"   # image tags are published without the leading 'v' (see release.yml)

cd "$REMOTE_PATH"

# Bring the stack back if any step below fails. upgrade.sh recreates services
# one at a time, so a failure partway through leaves the earlier ones stopped —
# a failed deploy became an outage that the next deploy could not clear on its
# own. Best-effort and non-masking: the original exit status is what the
# workflow sees, so a recovered stack still reports a red deploy.
restore_stack_on_failure() {
  rc=$?
  [ "$rc" -eq 0 ] && exit 0
  echo "==> deploy failed (exit $rc) — attempting to restart the stack" >&2
  if docker-compose -f docker-compose.yml up -d >&2; then
    echo "==> stack is back up; the deploy itself still FAILED (exit $rc)" >&2
  else
    echo "==> could not restart the stack — it needs manual attention" >&2
  fi
  exit "$rc"
}
trap restore_stack_on_failure EXIT

echo "==> fetching tags"
git fetch --tags --prune --force

echo "==> checking out $TAG"
# -f: the demo runs read-only with a daily reseed, so the working tree only ever
# holds our committed deploy files; a forced checkout is safe and avoids a snag
# on any local drift. .env is gitignored and therefore untouched by this.
git checkout -f "tags/$TAG"
# Log the resolved commit so a re-pointed/moved tag is auditable in the deploy
# logs (the fetch uses --force, so a moved upstream tag is accepted silently).
echo "    $TAG resolves to commit $(git rev-parse --short HEAD)"

echo "==> pinning IMAGE_TAG=$IMG in .env"
if [ ! -f .env ]; then
  echo "remote-deploy: .env not found in $REMOTE_PATH — run scripts/install.sh first" >&2
  exit 1
fi
# IMG is a validated digit/dot semver, so it carries no sed metacharacters.
if grep -qE '^IMAGE_TAG=' .env; then
  tmp="$(mktemp)"
  sed "s/^IMAGE_TAG=.*/IMAGE_TAG=${IMG}/" .env > "$tmp" && mv "$tmp" .env
else
  printf 'IMAGE_TAG=%s\n' "$IMG" >> .env
fi

echo "==> running scripts/upgrade.sh (non-interactive)"
# NO_PROMPT=1        — answer every upgrade.sh prompt with its safe default.
# UPGRADE_SKIP_DRAIN=1 — the public demo runs no long scans; skip the queue-drain
#                        wait. (On a fresh demo the v2.3→v2.4 DT prelude is skipped
#                        entirely anyway — no DT artefacts exist.)
NO_PROMPT=1 UPGRADE_SKIP_DRAIN=1 bash scripts/upgrade.sh

# Optional demo reseed. OFF by default: reset_demo DROPS and rebuilds the demo
# dataset, so it must never be something a routine deploy does silently.
#
# It is needed when a release changes what seed_demo.py produces. The login page
# advertises `explore@demo.trustedoss.dev`, and that account existed in the code
# for releases before it existed in the database — visitors were handed
# credentials that returned "invalid email or password". A new image alone does
# not reseed; the daily timer eventually does, which leaves a window measured in
# hours where the demo's own instructions are wrong.
#
# Scope: reset_demo deletes only what belongs to the demo organisation, in one
# transaction. Accounts outside it are untouched.
if [ "${RESEED:-0}" = "1" ]; then
  echo "==> reseeding the demo dataset (RESEED=1)"
  compose_args="-f docker-compose.yml"
  if [ -f .env ] && grep -qE '^COMPOSE_FILE=' .env; then
    compose_args=""   # docker-compose reads COMPOSE_FILE from .env itself
  fi
  # shellcheck disable=SC2086  # compose_args is our own literal, word-splitting intended.
  docker-compose $compose_args exec -T -e APP_ENV=demo backend python -m scripts.reset_demo
  echo "==> reseed complete"
else
  echo "==> skipping demo reseed (set RESEED=1 to rebuild the demo dataset)"
fi

echo "==> deploy of $TAG complete"
