#!/usr/bin/env bash
# Provision a freshly-started Dependency-Track for e2e: complete the forced
# first-login password change and mint an API key, then write it into .env so
# the portal backend can reach DT.
#
# Idempotent-ish: if admin/admin no longer works (already provisioned) it tries
# the configured DT_ADMIN_PASSWORD. Prints the key and updates .env in place.
#
# Env: DT_BASE (default http://localhost:8080), DT_ADMIN_PASSWORD
# (default ChangeMe-e2e-12345!), ENV_FILE (default .env).
set -euo pipefail
DT_BASE="${DT_BASE:-http://localhost:8080}"
NEWPW="${DT_ADMIN_PASSWORD:-ChangeMe-e2e-12345!}"
ENV_FILE="${ENV_FILE:-.env}"

echo "waiting for DT at $DT_BASE ..."
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$DT_BASE/api/version" || echo 000)
  [ "$code" = "200" ] && { echo "DT up (${i}x3s)"; break; }
  sleep 3
done

login() { curl -s -X POST "$DT_BASE/api/v1/user/login" \
  --data-urlencode "username=admin" --data-urlencode "password=$1"; }

JWT="$(login "$NEWPW")"
case "$JWT" in
  ey*) echo "admin already provisioned" ;;
  *)
    echo "completing forced first-login password change"
    curl -s -o /dev/null -w 'forceChangePassword=%{http_code}\n' \
      -X POST "$DT_BASE/api/v1/user/forceChangePassword" \
      --data-urlencode "username=admin" --data-urlencode "password=admin" \
      --data-urlencode "newPassword=$NEWPW" --data-urlencode "confirmPassword=$NEWPW"
    JWT="$(login "$NEWPW")"
    case "$JWT" in ey*) : ;; *) echo "DT login failed: ${JWT:0:80}"; exit 1 ;; esac
    ;;
esac

UUID="$(curl -s -H "Authorization: Bearer $JWT" "$DT_BASE/api/v1/team" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);t=[x for x in d if x.get('name')=='Administrators'] or d;print(t[0]['uuid'])")"
KEY="$(curl -s -X PUT -H "Authorization: Bearer $JWT" "$DT_BASE/api/v1/team/$UUID/key" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('key') or d.get('apikey') or '')")"
[ -n "$KEY" ] || { echo "API key mint failed"; exit 1; }
echo "minted DT API key (len ${#KEY})"

if grep -q '^DT_API_KEY=' "$ENV_FILE" 2>/dev/null; then
  python3 - "$ENV_FILE" "$KEY" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
out = [("DT_API_KEY=" + key) if ln.startswith("DT_API_KEY=") else ln for ln in lines]
open(path, "w").write("\n".join(out) + "\n")
PY
else
  printf 'DT_API_KEY=%s\n' "$KEY" >> "$ENV_FILE"
fi
echo "DT_API_KEY written to $ENV_FILE"
