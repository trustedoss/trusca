#!/usr/bin/env node
/**
 * release-refs self-test: the linter's own contract, exercised on fixtures.
 *
 * Same shape as tools/em-dash/selftest.mjs. It runs the pure functions, so it
 * needs no git state and no particular checkout. Run it with
 * `node tools/release-refs/selftest.mjs`.
 */
import {
  isVersionAgnosticRef,
  pinFindings,
  refFindings,
  releaseVersionFrom,
} from "./lint.mjs";

let failures = 0;

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(`  expected ${JSON.stringify(expected)}`);
    console.error(`  actual   ${JSON.stringify(actual)}`);
  }
}

const V = "0.22.4";
const found = (text) => refFindings(text, V).map((f) => f.found);

// --- source of truth -------------------------------------------------------

check(
  "release version skips the Unreleased holding area",
  releaseVersionFrom("# Changelog\n\n## [Unreleased]\n\n## [0.22.4] - 2026-09-02\n\n## [0.22.3] - x\n"),
  "0.22.4",
);
check(
  "a prerelease still counts as the newest release",
  releaseVersionFrom("## [Unreleased]\n## [1.0.0-rc.1] - x\n"),
  "1.0.0-rc.1",
);

// --- refs that are correct whatever the release is -------------------------

check("main is a living branch", isVersionAgnosticRef("main"), true);
check(
  "a full commit SHA is immutable",
  isVersionAgnosticRef("176bc3f0632bf0cf209c443da308e3d863dfde44"),
  true,
);
check(
  "an angle-bracket placeholder is prose",
  isVersionAgnosticRef("<full-commit-sha>"),
  true,
);
check("a short SHA is not a pin", isVersionAgnosticRef("a1b2c3d4e5f6"), false);
check("a release tag is checked", isVersionAgnosticRef("v0.10.0"), false);

// --- the reference forms a reader executes ---------------------------------

check(
  "raw URL with a path after the ref",
  found("  - remote: 'https://raw.githubusercontent.com/trustedoss/trusca/v0.10.0/templates/gitlab-ci.yml'"),
  ["v0.10.0"],
);
// The regression that shipped: the pattern required a slash after the ref, so
// a bare assignment at end of line was invisible to the whole check.
check(
  "raw URL with the ref at end of line",
  found("BASE=https://raw.githubusercontent.com/trustedoss/trusca/v0.10.0"),
  ["v0.10.0"],
);
check(
  "blob URL on main is left alone",
  found("[SECURITY.md](https://github.com/trustedoss/trusca/blob/main/SECURITY.md)"),
  [],
);
check(
  "composite action ref",
  found("        uses: trustedoss/trusca/actions/scan@v0.10.0"),
  ["v0.10.0"],
);
check(
  "composite action pinned to a full SHA",
  found("- uses: trustedoss/trusca/actions/scan@176bc3f0632bf0cf209c443da308e3d863dfde44  # v0.22.4"),
  [],
);
check("git checkout of a tag", found("git checkout v0.10.0"), ["v0.10.0"]);
check(
  "the current release is not a finding",
  found("uses: trustedoss/trusca/actions/scan@v0.22.4\ngit checkout v0.22.4"),
  [],
);

// --- what must NOT fire ----------------------------------------------------

// Release history is narrative. Rewriting it would make the sentence false.
check(
  "a version named in prose is history, not an instruction",
  found("v0.10.0 removed Dependency-Track in favour of Trivy."),
  [],
);
check(
  "an in-toto buildType URI is not a git ref",
  found('"https://github.com/trustedoss/trusca/buildtypes/source-scan@v1"'),
  [],
);
check(
  "a third-party action is none of our business",
  found("      - uses: actions/checkout@v4"),
  [],
);

// --- bare literals, which only an explicit pin can find --------------------

check(
  "a stale pinned literal is reported with its line",
  pinFindings("a\nb\nversion: 0.22.0\n", V, "^version: (\\d+\\.\\d+\\.\\d+)$").map(
    (f) => [f.line, f.found],
  ),
  [[3, "0.22.0"]],
);
check(
  "a current pinned literal is silent",
  pinFindings("version: 0.22.4\n", V, "^version: (\\d+\\.\\d+\\.\\d+)$"),
  [],
);
check(
  "every occurrence of a repeated pin is reported",
  pinFindings(
    "image: a:${IMAGE_TAG:-0.12.0}\nimage: b:${IMAGE_TAG:-0.12.0}\n",
    V,
    "\\$\\{IMAGE_TAG:-(\\d+\\.\\d+\\.\\d+)\\}",
  ).length,
  2,
);

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("release-refs selftest: all checks passed");
