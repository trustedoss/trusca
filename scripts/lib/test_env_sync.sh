#!/usr/bin/env bash
# Unit tests for scripts/lib/env_sync.sh — W6-chore-seed B.
#
# Pure bash test harness — no pytest dependency so CI can invoke it directly
# from the lint stage. Each test seeds /tmp dirs, runs the sync helper, and
# asserts on the resulting file content. Returns non-zero on any failure.
#
# Usage:
#   bash scripts/lib/test_env_sync.sh

set -euo pipefail

# Resolve the helper relative to this script's own directory so the suite
# works regardless of the caller's cwd.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/env_sync.sh
source "$SCRIPT_DIR/env_sync.sh"

PASS=0
FAIL=0
FAILURES=()

_assert() {
  local condition="$1"
  local description="$2"
  if eval "$condition"; then
    PASS=$((PASS + 1))
    return 0
  fi
  FAIL=$((FAIL + 1))
  FAILURES+=("$description")
  echo "  FAIL: $description"
  return 1
}

_mktemp_dir() {
  mktemp -d "/tmp/env_sync_test.XXXXXX"
}

# ---------------------------------------------------------------------------
# Test 1 — happy path: new key appended, existing untouched
# ---------------------------------------------------------------------------
echo "TEST 1: appends missing keys, preserves existing values"
T1=$(_mktemp_dir)
cat >"$T1/example.env" <<'EOF'
EXISTING=ignored_in_target
NEW_KEY=new_value
EOF
cat >"$T1/.env" <<'EOF'
EXISTING=user_value
EOF
env_append_only_sync "$T1/example.env" "$T1/.env" >/dev/null

_assert "grep -qxF 'EXISTING=user_value' '$T1/.env'" \
  "existing value preserved verbatim"
_assert "grep -qxF 'NEW_KEY=new_value' '$T1/.env'" \
  "new key appended"
rm -rf "$T1"

# ---------------------------------------------------------------------------
# Test 2 — idempotency: second run appends nothing
# ---------------------------------------------------------------------------
echo "TEST 2: second run is a no-op"
T2=$(_mktemp_dir)
cat >"$T2/example.env" <<'EOF'
ONLY_KEY=v
EOF
cat >"$T2/.env" <<'EOF'
EOF
env_append_only_sync "$T2/example.env" "$T2/.env" >/dev/null
first_size=$(wc -c <"$T2/.env")
env_append_only_sync "$T2/example.env" "$T2/.env" >/dev/null
second_size=$(wc -c <"$T2/.env")

_assert "[[ '$first_size' == '$second_size' ]]" \
  "idempotent: second run produced byte-identical file"
rm -rf "$T2"

# ---------------------------------------------------------------------------
# Test 3 — commented-out key in target counts as "present"
# ---------------------------------------------------------------------------
echo "TEST 3: commented-out target key is NOT re-appended"
T3=$(_mktemp_dir)
cat >"$T3/example.env" <<'EOF'
TOGGLE=on
EOF
cat >"$T3/.env" <<'EOF'
# TOGGLE=off
EOF
env_append_only_sync "$T3/example.env" "$T3/.env" >/dev/null

_assert "! grep -qxF 'TOGGLE=on' '$T3/.env'" \
  "uncommented duplicate NOT appended when commented form exists"
_assert "grep -qxF '# TOGGLE=off' '$T3/.env'" \
  "operator's commented-out line preserved"
rm -rf "$T3"

# ---------------------------------------------------------------------------
# Test 4 — missing target file is a no-op (helper is sync, not bootstrap)
# ---------------------------------------------------------------------------
echo "TEST 4: missing target → no-op, no crash"
T4=$(_mktemp_dir)
cat >"$T4/example.env" <<'EOF'
ANYTHING=ok
EOF
env_append_only_sync "$T4/example.env" "$T4/.env" >/dev/null

_assert "[[ ! -f '$T4/.env' ]]" \
  "missing target stays missing (no bootstrap)"
rm -rf "$T4"

# ---------------------------------------------------------------------------
# Test 5 — missing example file is a no-op
# ---------------------------------------------------------------------------
echo "TEST 5: missing example → no-op, no crash"
T5=$(_mktemp_dir)
cat >"$T5/.env" <<'EOF'
ALREADY=here
EOF
env_append_only_sync "$T5/example.env" "$T5/.env" >/dev/null

_assert "grep -qxF 'ALREADY=here' '$T5/.env'" \
  "target unchanged when example missing"
rm -rf "$T5"

# ---------------------------------------------------------------------------
# Test 6 — comment grouping: a missing key drags its leading comment block
# ---------------------------------------------------------------------------
echo "TEST 6: leading comment block follows the missing key into target"
T6=$(_mktemp_dir)
cat >"$T6/example.env" <<'EOF'
EXISTING=x
# ---- new W6 section ----
# Describes the new tunable
NEW_TUNABLE=42
EOF
cat >"$T6/.env" <<'EOF'
EXISTING=keep
EOF
env_append_only_sync "$T6/example.env" "$T6/.env" >/dev/null

_assert "grep -qxF '# Describes the new tunable' '$T6/.env'" \
  "comment block describing the new key carried through"
_assert "grep -qxF 'NEW_TUNABLE=42' '$T6/.env'" \
  "new key appended after its comment"
rm -rf "$T6"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "==================== summary ===================="
echo "passed: $PASS"
echo "failed: $FAIL"
if (( FAIL > 0 )); then
  printf '  - %s\n' "${FAILURES[@]}"
  exit 1
fi
echo "all green"
