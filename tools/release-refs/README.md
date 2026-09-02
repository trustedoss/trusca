# release-refs

Keeps every reference a reader executes pointed at a tag that exists.

## Why

The repository has been recreated twice, and each recreation dropped the tags
that came before it. Only `v0.20.0` and later resolve today. The documentation
did not move with them, so the CI integration guides, the Compose install page
and the composite action's README all pinned their copy-paste examples to
`v0.10.0`:

- `uses: trustedoss/trusca/actions/scan@v0.10.0` fails with "unable to resolve
  action".
- The GitLab `include: remote:` URL and the `curl` quick install return 404.
- `git checkout v0.10.0` fails on a fresh clone.

Twenty-eight places, in English and Korean, and all of them are the first
thing someone evaluating the portal runs. Editing the values fixes today and
nothing else, so the version lives in one place instead and every reference to
it is derived.

## Source of truth

The newest released section in `CHANGELOG.md`, skipping `[Unreleased]`.

Not a git tag: the tag is created after the release commit merges, so a
tag-derived check would be unrunnable exactly when it matters. Not a constant
of its own: that is a second place to forget.

## What it checks

**Tag-shaped refs into this repository**, found by pattern, so a reference
written next year is covered the day it is written. A raw githubusercontent
URL, a `blob` / `tree` URL, a `uses:` ref into `actions/`, a `git checkout`.
`main`, a full 40-character commit SHA, and an angle-bracket placeholder are
all deliberate choices and pass; a `vX.Y.Z` that is not the current release
fails.

The patterns deliberately do not match a bare version in prose. "v0.10.0
removed Dependency-Track" is history, and rewriting it would make the sentence
false.

**Bare version literals** listed in `pins.json`, which no pattern can tell
apart from any other number: the chart version and `appVersion`, the chart's
`image.tag` default, the README release badge, the values table in the Helm
guide and its Korean mirror.

`CHANGELOG.md` and the release-notes pages are excluded for the same reason
the prose patterns are narrow.

## Usage

```bash
node tools/release-refs/lint.mjs           # report drift, exit 1 if any
node tools/release-refs/lint.mjs --fix     # rewrite to the current release
node tools/release-refs/lint.mjs --version # print the release it derived
node tools/release-refs/selftest.mjs       # the linter's own contract
```

`--fix` rewrites only the line each finding sits on. A whole-file replace
would also rewrite the same digits where they mean something else.

## Where it runs

- `lint (frontend)` in `.github/workflows/ci.yml`, a required check, so a PR
  cannot merge with a reference that does not resolve.
- `scripts/release.sh`, before the tag is created, so a release cannot be cut
  while the guides still name the previous one.

## Bumping the version

Add the `## [X.Y.Z]` section to `CHANGELOG.md`, run
`node tools/release-refs/lint.mjs --fix`, and commit both. The window in which
the documentation names a tag that has not been pushed yet is the few minutes
between that merge and `scripts/release.sh` pushing the tag.

## pending

`pins.json` carries a `pending` list: entries that belong in `pins` but whose
files were held by other work in flight when the check was added. The linter
prints them as a notice and does not fail on them. Moving an entry up into
`pins` is the whole of the follow-up.
