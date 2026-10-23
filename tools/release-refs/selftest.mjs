#!/usr/bin/env node
/**
 * release-refs self-test: the linter's own contract, exercised on fixtures.
 *
 * Same shape as tools/em-dash/selftest.mjs. Most checks run the pure
 * functions. The command-line checks at the end run lint.mjs in a throwaway
 * git repository built in a temp directory, so they need `git` on PATH but
 * no particular checkout. Run it with `node tools/release-refs/selftest.mjs`.
 */
import {
  isVersionAgnosticRef,
  pinFindings,
  refFindings,
  releaseGateFindings,
  releaseVersionFrom,
} from "./lint.mjs";
import { execFileSync, spawnSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

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
// Install defaults never move to a release candidate. If they did, --fix
// would write IMAGE_TAG=0.24.0-rc.1 and every X.Y.Z pin would match nothing.
check(
  "a pre-release heading is not the install release",
  releaseVersionFrom("## [Unreleased]\n## [0.24.0-rc.1] - x\n## [0.23.0] - y\n"),
  "0.23.0",
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

// --- release gate (--release vX.Y.Z) ---------------------------------------

check(
  "a tag that matches the changelog with nothing pending may publish",
  releaseGateFindings("v0.22.4", V, []),
  [],
);
check(
  "the leading v is optional on the tag",
  releaseGateFindings("0.22.4", V, []),
  [],
);
// The v0.23.0 failure: tag pushed, every default still named the old release.
check(
  "a tag ahead of the changelog is blocked",
  releaseGateFindings("v0.23.0", V, []).length,
  1,
);
check(
  "a parked pending pin blocks the release",
  releaseGateFindings("v0.22.4", V, [{ file: ".env.example", blocked_by: "PR #1" }]).map(
    (m) => m.includes(".env.example"),
  ),
  [true],
);

// --- the install defaults are enrolled, not parked -------------------------

// A fresh install pulls whatever these two name. They sat in `pending` for a
// release cycle and shipped stale, so their enrollment is part of the contract.
const config = JSON.parse(fs.readFileSync(new URL("./pins.json", import.meta.url), "utf8"));
for (const file of [".env.example", "docker-compose.yml"]) {
  check(
    `${file} is an enrolled pin`,
    config.pins.some((p) => p.file === file),
    true,
  );
}

check(
  "a pre-release tag is not compared to the newest stable entry",
  releaseGateFindings("v0.24.0-rc.1", "0.23.0", []),
  [],
);
check(
  "a pre-release tag is still blocked by a pending pin",
  releaseGateFindings("v0.24.0-rc.1", "0.23.0", [{ file: ".env.example", blocked_by: "x" }]).length,
  1,
);

// --- the command line, in a throwaway repository ---------------------------

// main() is what release.sh and release.yml run, so its argument parsing and
// exit codes are part of the contract, not only the pure functions above.
function fixtureRepo({ changelog, envTag, pending = [] }) {
  // realpath: lint.mjs only runs main() when argv[1] equals its own resolved
  // URL, and the macOS temp dir is reached through a /var symlink.
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "release-refs-")));
  const tool = path.join(root, "tools", "release-refs");
  fs.mkdirSync(tool, { recursive: true });
  fs.copyFileSync(new URL("./lint.mjs", import.meta.url), path.join(tool, "lint.mjs"));
  fs.writeFileSync(
    path.join(tool, "pins.json"),
    JSON.stringify({
      pins: [{ file: ".env.example", pattern: "^IMAGE_TAG=(\\d+\\.\\d+\\.\\d+)$", note: "n" }],
      pending,
      exclude: ["CHANGELOG.md", "tools/release-refs/"],
    }),
  );
  fs.writeFileSync(path.join(root, "CHANGELOG.md"), changelog);
  fs.writeFileSync(path.join(root, ".env.example"), `IMAGE_TAG=${envTag}\n`);
  execFileSync("git", ["init", "-q"], { cwd: root });
  execFileSync("git", ["add", "-A"], { cwd: root });
  return root;
}
function run(root, ...args) {
  const r = spawnSync(process.execPath, [path.join(root, "tools", "release-refs", "lint.mjs"), ...args], {
    cwd: root,
    encoding: "utf8",
  });
  return { code: r.status, err: r.stderr };
}
const STABLE = "## [Unreleased]\n## [0.23.0] - x\n";
const RC = "## [Unreleased]\n## [0.24.0-rc.1] - x\n## [0.23.0] - y\n";

{
  const root = fixtureRepo({ changelog: STABLE, envTag: "0.23.0" });
  check("--release without a tag is a usage error", run(root, "--release").code, 2);
  check("--release followed by a flag is a usage error", run(root, "--release", "--fix").code, 2);
  check("--release on the newest entry passes", run(root, "--release", "v0.23.0").code, 0);
  check("a tag ahead of the changelog fails with no drift", run(root, "--release", "v0.24.0").code, 1);
  check("a pre-release tag passes on stable defaults", run(root, "--release", "v0.24.0-rc.1").code, 0);
}
{
  // Gate and drift both present: one exit 1, gate reported first so the
  // root cause (the missing changelog bump) is not buried under the drift.
  const root = fixtureRepo({ changelog: STABLE, envTag: "0.22.0" });
  const r = run(root, "--release", "v0.24.0");
  check("gate plus drift exits 1", r.code, 1);
  check(
    "gate is reported before drift",
    r.err.indexOf("blocked") !== -1 && r.err.indexOf("blocked") < r.err.indexOf(".env.example:1"),
    true,
  );
}
{
  const root = fixtureRepo({
    changelog: STABLE,
    envTag: "0.23.0",
    pending: [{ file: "x.yml", pattern: "(\\d+\\.\\d+\\.\\d+)", blocked_by: "PR #1" }],
  });
  check("a pending pin passes on a PR", run(root).code, 0);
  check("a pending pin fails the release", run(root, "--release", "v0.23.0").code, 1);
}
{
  // An rc heading on top must not move the install default.
  const root = fixtureRepo({ changelog: RC, envTag: "0.22.0" });
  run(root, "--fix");
  check(
    "--fix under an rc heading writes the newest stable release",
    fs.readFileSync(path.join(root, ".env.example"), "utf8"),
    "IMAGE_TAG=0.23.0\n",
  );
}

if (failures > 0) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log("release-refs selftest: all checks passed");
