#!/usr/bin/env bash
# TrustedOSS Portal — git tag + GitHub Release helper.
#
# Usage:
#   bash scripts/release.sh v2.0.0-rc.1
#
# Pre-conditions:
#   - main branch up to date and clean.
#   - CHANGELOG.md has an entry for the version (matched by `## [<ver>]`).
#   - You are authenticated with `gh` (gh auth status).
#
# Steps:
#   1. Validate the tag is SemVer (`vX.Y.Z` or `vX.Y.Z-rc.N`).
#   2. Verify CHANGELOG.md contains the version.
#   3. Confirm with the operator.
#   4. Create an annotated tag pointing at HEAD of main.
#   5. Push the tag.
#   6. Create a GitHub Release whose body is the CHANGELOG section.

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
BOLD='\033[1m'
RESET='\033[0m'

ok()    { printf "${GREEN}✓${RESET} %s\n" "$1"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$1" >&2; exit 1; }
title() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

[[ $# -eq 1 ]] || fail "usage: bash scripts/release.sh <tag>  (e.g. v2.0.0-rc.1)"
TAG="$1"

# 1. SemVer validation
if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-(alpha|beta|rc)\.[0-9]+)?$ ]]; then
  fail "tag must be SemVer-shaped: vX.Y.Z or vX.Y.Z-{alpha,beta,rc}.N"
fi
ok "tag format: $TAG"

# 2. Working tree must be clean and on main
[[ -z "$(git status --porcelain)" ]] || fail "working tree is dirty. Commit or stash first."
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || fail "must be on main."
ok "on clean main"

# 3. CHANGELOG.md entry
version_no_v="${TAG#v}"
grep -q "^## \[$version_no_v\]" CHANGELOG.md \
  || fail "CHANGELOG.md has no '## [$version_no_v]' section. Add release notes first."
ok "CHANGELOG entry found"

# 4. Tag does not already exist
if git rev-parse "$TAG" >/dev/null 2>&1; then
  fail "tag $TAG already exists locally. Aborting."
fi
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q "$TAG"; then
  fail "tag $TAG already exists on origin. Aborting."
fi
ok "tag is new"

# 5. Confirm
echo
git log --oneline -5
echo
read -r -p "Tag main@$(git rev-parse --short HEAD) as $TAG and create a GitHub Release? [y/N] " reply
[[ "$reply" =~ ^[Yy]$ ]] || fail "aborted"

# 6. Annotated tag + push
title "Creating annotated tag"
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"
ok "tag $TAG pushed"

# 7. Extract CHANGELOG body
title "Building release body from CHANGELOG"
body=$(awk -v v="$version_no_v" '
  $0 ~ "^## \\[" v "\\]" { found = 1; next }
  found && /^## \[/      { exit }
  found                  { print }
' CHANGELOG.md)
[[ -n "$body" ]] || fail "CHANGELOG body is empty for $version_no_v"

# 8. GitHub Release
title "Creating GitHub Release"
gh release create "$TAG" \
  --title "$TAG" \
  --notes "$body" \
  $(if [[ "$TAG" =~ -(alpha|beta|rc)\. ]]; then echo "--prerelease"; fi)
ok "GitHub Release created"

title "Release complete"
gh release view "$TAG" --json url --jq .url
