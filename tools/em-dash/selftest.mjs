#!/usr/bin/env node
/**
 * em-dash self-test: the linter's own contract, exercised on fixtures.
 *
 * Same shape as tools/ko-style/selftest.mjs. It runs the pure functions, so
 * it needs no git state and no repository checkout to be in any particular
 * condition. Run it with `node tools/em-dash/selftest.mjs`.
 */
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import {
  addedLines,
  allowReason,
  findings,
  isPlaceholderOnly,
  isWatched,
} from "./lint.mjs";

let failures = 0;

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(`  expected ${JSON.stringify(expected)}`);
    console.error(`  actual   ${JSON.stringify(actual)}`);
  } else {
    console.log(`ok   ${name}`);
  }
}

// --- what counts as a file we author prose in -------------------------------

check("watches TypeScript", isWatched("apps/frontend/src/a.tsx"), true);
check("watches Python", isWatched("apps/backend/api/v1/a.py"), true);
check("watches locale catalogues", isWatched("src/locales/ko/a.json"), true);
check("ignores lock files", isWatched("package-lock.json".replace(".json", ".lock")), false);
check("ignores binary snapshots", isWatched("a.png"), false);
check(
  "ignores the visual baselines",
  isWatched("apps/frontend/tests/visual/visual.spec.ts-snapshots/x.ts"),
  false,
);
check(
  "ignores the translated docs mirror, which ko-style owns",
  isWatched("docs-site/i18n/ko/x.md"),
  false,
);
check(
  "ignores its own directory, whose subject is the character",
  isWatched("tools/em-dash/lint.mjs"),
  false,
);

// --- the placeholder exemption ----------------------------------------------

check("exempts a JSON placeholder value", isPlaceholderOnly('  "no_detail": "—",'), true);
check("exempts a bare quoted glyph", isPlaceholderOnly('    "—",'), true);
check(
  "does not exempt a sentence that contains one",
  isPlaceholderOnly('  "hint": "Off — bundled snapshot only",'),
  false,
);
check(
  "does not exempt a comment that contains one",
  isPlaceholderOnly("// A4 — see the other block"),
  false,
);

// --- the allow marker -------------------------------------------------------

check(
  "accepts a marker with a real reason",
  allowReason("const DASH = \"—\"; // em-dash-allow: renders the empty cell glyph"),
  "renders the empty cell glyph",
);
check(
  "rejects a marker with a token reason",
  allowReason("// em-dash-allow: needed"),
  null,
);
check(
  "strips a block comment terminator from the reason",
  allowReason("/* em-dash-allow: this is the placeholder glyph */"),
  "this is the placeholder glyph",
);

// --- diff parsing -----------------------------------------------------------

const DIFF = [
  "diff --git a/a.ts b/a.ts",
  "--- a/a.ts",
  "+++ b/a.ts",
  "@@ -1,0 +2,2 @@",
  "+// one — two",
  "+const ok = 1;",
  "diff --git a/b.png b/b.png",
  "--- a/b.png",
  "+++ b/b.png",
  "@@ -0,0 +1 @@",
  "+binary — not prose",
].join("\n");

check("reads the added lines and their numbers", addedLines(DIFF), [
  { file: "a.ts", line: 2, text: "// one — two" },
  { file: "a.ts", line: 3, text: "const ok = 1;" },
  { file: "b.png", line: 1, text: "binary — not prose" },
]);

check(
  "reports only the watched file's prose line",
  findings(addedLines(DIFF), () => null).map((f) => `${f.file}:${f.line}`),
  ["a.ts:2"],
);

check(
  "a marker on the line above clears it",
  findings(addedLines(DIFF), () => "// em-dash-allow: quoting a spec verbatim here").length,
  0,
);

// --- the real regression this gate exists for -------------------------------
// Every one of these shipped in this repository before the gate existed.

const REAL = [
  { file: "x.tsx", line: 1, text: "  // A4 — a bad finding id used to get a red banner" },
  { file: "x.ts", line: 2, text: " * NotFoundPage — the catch-all for an unmatched address." },
  { file: "x.py", line: 3, text: "# CVE triage — bumped 1.25.12 to 1.25.13" },
];
check(
  "catches the shapes that actually shipped",
  findings(REAL, () => null).length,
  3,
);

// --- the file git does not know about yet ----------------------------------
// This is the hole the gate shipped with: `git diff` reports nothing for an
// untracked file, so a brand-new one passed right up until it was committed,
// which is the moment the author is least likely to run the check again. The
// first new file written after the gate shipped went straight through it.
//
// Driven end to end in a scratch repository, because the defect was in which
// files the run collects, not in any of the pure functions above.

function runInScratchRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "em-dash-selftest-"));
  const git = (args) =>
    execFileSync("git", args, { cwd: dir, encoding: "utf8" });
  try {
    git(["init", "-q"]);
    git(["config", "user.email", "selftest@example.com"]);
    git(["config", "user.name", "selftest"]);
    fs.writeFileSync(path.join(dir, "seed.txt"), "seed\n");
    git(["add", "."]);
    git(["commit", "-qm", "seed"]);
    git(["branch", "-M", "main"]);
    git(["checkout", "-qb", "work"]);

    const linter = path.join(__dirname, "lint.mjs");
    fs.writeFileSync(
      path.join(dir, "new.ts"),
      `// a comment with an ${EM_DASH_LITERAL} em dash\n`,
    );

    let exitCode = 0;
    try {
      execFileSync(process.execPath, [linter, "--base", "main"], {
        cwd: dir,
        encoding: "utf8",
        stdio: "pipe",
      });
    } catch (err) {
      exitCode = err.status ?? 1;
    }
    return exitCode;
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

const __dirname = path.dirname(new URL(import.meta.url).pathname);
const EM_DASH_LITERAL = String.fromCharCode(0x2014);

check("catches an em dash in a file git is not tracking yet", runInScratchRepo(), 1);

console.log(failures === 0 ? "\nem-dash selftest: OK" : `\nem-dash selftest: ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
