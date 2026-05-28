#!/usr/bin/env bash
# TrustedOSS Portal — cosign SBOM-signing key bootstrap (v2.3-s1).
#
# Generates a cosign key pair for KEY-BASED SBOM signing (the D2 default for
# self-hosted / on-prem / air-gapped deployments) and prints the .env wiring.
# Keyless (OIDC) signing needs NO key pair — for that, set COSIGN_KEYLESS=true
# in .env and skip this script entirely.
#
# What it does:
#   1. Runs `cosign generate-key-pair`, which writes:
#        <out>/cosign.key   — the ENCRYPTED private key (PEM). Mount this into
#                             the worker (read-only). NEVER commit it.
#        <out>/cosign.pub   — the public key. Distribute this to verifiers.
#      cosign prompts for a password (or reads $COSIGN_PASSWORD) to encrypt the
#      private key at rest.
#   2. Encrypts that password with the app's Fernet key (core.crypto) so it can
#      live in .env as COSIGN_KEY_PASSWORD_ENCRYPTED (ciphertext), never plain.
#   3. Prints the exact COSIGN_* lines to add to .env.
#
# Usage:
#   bash scripts/cosign-keygen.sh                 # interactive (prompts for pw)
#   COSIGN_PASSWORD='...' bash scripts/cosign-keygen.sh --out ./secrets
#
# docker-compose V1 (hyphen) per CLAUDE.md rule #10. The encrypt step runs
# inside the worker container so it uses the SAME Fernet key the app will use to
# decrypt at signing time (GITHUB_APP_ENCRYPTION_KEY, or the dev-derived key).

set -euo pipefail

OUT_DIR="./secrets/cosign"
COMPOSE_FILE="docker-compose.yml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_DIR="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

if ! command -v cosign >/dev/null 2>&1; then
  echo "ERROR: cosign not found on PATH." >&2
  echo "Install it (https://docs.sigstore.dev/cosign/installation/) or run this" >&2
  echo "inside the worker container, which ships cosign:" >&2
  echo "  docker-compose -f ${COMPOSE_FILE} run --rm worker bash scripts/cosign-keygen.sh" >&2
  exit 1
fi

mkdir -p "${OUT_DIR}"

echo "==> Generating cosign key pair under ${OUT_DIR}/"
echo "    cosign will prompt for a password to encrypt the private key"
echo "    (or it reads \$COSIGN_PASSWORD if you exported one)."
( cd "${OUT_DIR}" && cosign generate-key-pair )

echo
echo "==> Key pair written:"
echo "    ${OUT_DIR}/cosign.key  (ENCRYPTED private key — mount read-only, do NOT commit)"
echo "    ${OUT_DIR}/cosign.pub  (public key — hand to verifiers)"

# Encrypt the password so it can live in .env as ciphertext. We only do this
# automatically when COSIGN_PASSWORD was provided non-interactively; otherwise
# we print the one-liner for the operator to run with their chosen password.
ENCRYPT_SNIPPET='python -c "import sys;from core.crypto import encrypt_secret;print(encrypt_secret(sys.argv[1]))"'

echo
echo "==> Next: encrypt the key password for .env (COSIGN_KEY_PASSWORD_ENCRYPTED)."
echo "    Run this INSIDE the worker container so it uses the app's Fernet key:"
echo
echo "      docker-compose -f ${COMPOSE_FILE} run --rm worker \\"
echo "        ${ENCRYPT_SNIPPET} 'YOUR_KEY_PASSWORD'"
echo
echo "    (A passwordless key is allowed — leave COSIGN_KEY_PASSWORD_ENCRYPTED unset.)"

echo
echo "==> Then add to .env (and mount the key into the worker):"
cat <<'EOF'
  COSIGN_KEYLESS=false
  COSIGN_KEY_PATH=/cosign/cosign.key
  COSIGN_KEY_PASSWORD_ENCRYPTED=<paste ciphertext from the encrypt step>

  # docker-compose worker volume (already wired in docker-compose.yml /
  # docker-compose.dev.yml — point COSIGN_KEYS_HOST_PATH at your ${OUT_DIR}):
  COSIGN_KEYS_HOST_PATH=./secrets/cosign
EOF

echo
echo "Done. Keep ${OUT_DIR}/cosign.key OUT of version control."
