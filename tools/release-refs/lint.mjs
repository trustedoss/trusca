#!/usr/bin/env node
/**
 * release-refs: keeps every reference a reader executes pointed at a tag that
 * exists.
 *
 * The repository has been recreated twice, and each recreation dropped the
 * tags that came before it. The documentation did not move with them: the CI
 * integration guides, the Compose install page and the composite action's
 * README all pinned their copy-paste examples to `v0.10.0`, a tag that no
 * longer resolves. A reader following the GitHub Actions quickstart got
 * "unable to resolve action"; the GitLab include and the `curl` install both
 * fetched a 404. Twenty-eight places, all of them the first thing an
 * evaluator runs.
 *
 * Editing those twenty-eight values fixes today and nothing else, so this
 * check exists instead: the version lives in one place, and every reference
 * to it is derived rather than remembered.
 *
 * Source of truth: the newest released section in CHANGELOG.md. Not a git tag
 * (the tag is created after the release commit merges, so the check would be
 * unrunnable exactly when it matters) and not a constant of its own (a second
 * place to forget).
 *
 * Two kinds of reference are checked:
 *
 *   1. Tag-shaped refs into this repository, found by pattern. A raw
 *      githubusercontent URL, a `blob`/`tree` URL, a `uses:` ref into
 *      actions/, a `git checkout`. `main` and full commit SHAs are fine;
 *      an angle-bracket placeholder is fine. A `vX.Y.Z` must be the current
 *      release. Nothing else has to be registered anywhere, so a reference
 *      added next year is covered the day it is written.
 *
 *   2. Bare version literals listed in pins.json, which no pattern can tell
 *      apart from any other number: the chart version, the image tag
 *      defaults, the README badge.
 *
 * Release notes and the changelog are excluded: their tag references are
 * history, and rewriting them would be a lie.
 *
 * Pure Node ESM, no dependencies, same shape as tools/em-dash/lint.mjs.
 *
 * Usage:
 *   node tools/release-refs/lint.mjs            # report drift, exit 1 if any
 *   node tools/release-refs/lint.mjs --fix      # rewrite to the current release
 *   node tools/release-refs/lint.mjs --version  # print the release it derived
 */
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const REPO_ROOT = (() => {
  try {
    return execFileSync("git", ["rev-parse", "--show-toplevel"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return path.resolve(__dirname, "..", "..");
  }
})();

/** Text files worth scanning. Anything else cannot carry a reference a reader runs. */
const SCANNED_EXTENSIONS = new Set([
  ".md",
  ".mdx",
  ".yml",
  ".yaml",
  ".json",
  ".sh",
  ".ts",
  ".tsx",
  ".js",
  ".mjs",
  ".py",
  ".example",
  ".template",
]);

/**
 * Refs that are correct whatever the release is.
 *
 * `main` is a living branch, a full commit SHA is immutable, and an
 * angle-bracket placeholder is prose telling the reader to substitute
 * something. Each is a deliberate choice rather than a stale value.
 */
export function isVersionAgnosticRef(ref) {
  return ref === "main" || /^[0-9a-f]{40}$/.test(ref) || /^<.+>$/.test(ref);
}

/**
 * Reference forms a reader executes. Group `ref` is the git ref each resolves.
 *
 * Deliberately narrow: prose like "v0.10.0 removed Dependency-Track" is
 * history and must not match, so nothing here fires on a bare version.
 */
export const REF_PATTERNS = [
  // The lookahead, rather than a literal `/`, is what makes a ref at the end
  // of a line count: `BASE=https://raw.githubusercontent.com/.../v0.10.0` has
  // no trailing slash and was missed until it did.
  {
    name: "raw-url",
    re: /raw\.githubusercontent\.com\/trustedoss\/trusca\/(?<ref>[^/\s'"`)]+)(?=[/\s'"`)]|$)/g,
  },
  {
    name: "blob-url",
    re: /github\.com\/trustedoss\/trusca\/(?:blob|tree|raw)\/(?<ref>[^/\s'"`)]+)(?=[/\s'"`)]|$)/g,
  },
  {
    name: "action-ref",
    re: /trustedoss\/trusca\/actions\/[A-Za-z0-9._/-]+@(?<ref>[^\s'"`)]+)/g,
  },
  { name: "git-checkout", re: /git checkout (?<ref>v\d[^\s'"`)]*)/g },
  { name: "git-clone-branch", re: /git clone [^\n]*?-b (?<ref>v\d[^\s'"`)]*)/g },
];

/**
 * The newest released version in the changelog.
 *
 * `[Unreleased]` is skipped: it is a holding area, not a release, and pointing
 * an install command at it would send readers to a tag that does not exist.
 */
export function releaseVersionFrom(changelog) {
  for (const line of changelog.split("\n")) {
    const m = /^##\s+\[(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\]/.exec(line);
    if (m) return m[1];
  }
  throw new Error("CHANGELOG.md has no released `## [X.Y.Z]` section");
}

function isExcluded(relPath, exclude) {
  return exclude.some((e) => (e.endsWith("/") ? relPath.startsWith(e) : relPath === e));
}

/** Every tag-shaped ref in `text` that names a release other than `version`. */
export function refFindings(text, version) {
  const want = `v${version}`;
  const out = [];
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    for (const { name, re } of REF_PATTERNS) {
      re.lastIndex = 0;
      let m;
      while ((m = re.exec(line)) !== null) {
        const ref = m.groups.ref;
        if (isVersionAgnosticRef(ref) || ref === want) continue;
        out.push({ line: i + 1, kind: name, found: ref, want });
      }
    }
  });
  return out;
}

/** Every pinned literal in `text` that is not `version`. */
export function pinFindings(text, version, pattern) {
  const re = new RegExp(pattern, "gm");
  const out = [];
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m[1] === version) continue;
    const line = text.slice(0, m.index).split("\n").length;
    out.push({ line, kind: "pin", found: m[1], want: version });
  }
  return out;
}

function trackedFiles() {
  return execFileSync("git", ["ls-files"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  })
    .split("\n")
    .filter(Boolean);
}

/**
 * Rewrite one stale ref or literal in place, leaving everything else alone.
 *
 * Scoped to the finding's own line. A whole-file replace would also rewrite
 * the same digits where they mean something else, which is how a version
 * number in a narrative sentence gets silently rewritten into a lie.
 */
function applyFix(text, finding) {
  const lines = text.split("\n");
  const i = finding.line - 1;
  if (i < 0 || i >= lines.length) return text;
  lines[i] = lines[i].split(finding.found).join(finding.want);
  return lines.join("\n");
}

function main() {
  const args = process.argv.slice(2);
  const fix = args.includes("--fix");

  const config = JSON.parse(
    fs.readFileSync(path.join(__dirname, "pins.json"), "utf8"),
  );
  const version = releaseVersionFrom(
    fs.readFileSync(path.join(REPO_ROOT, "CHANGELOG.md"), "utf8"),
  );

  if (args.includes("--version")) {
    console.log(version);
    return 0;
  }

  const byFile = new Map();
  const add = (file, finding) => {
    if (!byFile.has(file)) byFile.set(file, []);
    byFile.get(file).push(finding);
  };

  for (const rel of trackedFiles()) {
    if (!SCANNED_EXTENSIONS.has(path.extname(rel))) continue;
    if (isExcluded(rel, config.exclude)) continue;
    const abs = path.join(REPO_ROOT, rel);
    let text;
    try {
      text = fs.readFileSync(abs, "utf8");
    } catch {
      continue;
    }
    for (const f of refFindings(text, version)) add(rel, f);
  }

  for (const pin of config.pins) {
    const abs = path.join(REPO_ROOT, pin.file);
    if (!fs.existsSync(abs)) {
      add(pin.file, {
        line: 0,
        kind: "pin",
        found: "(file missing)",
        want: version,
        note: pin.note,
      });
      continue;
    }
    const text = fs.readFileSync(abs, "utf8");
    const found = pinFindings(text, version, pin.pattern);
    if (found.length === 0 && !new RegExp(pin.pattern, "m").test(text)) {
      add(pin.file, {
        line: 0,
        kind: "pin",
        found: "(pattern matched nothing)",
        want: version,
        note: pin.note,
      });
      continue;
    }
    for (const f of found) add(pin.file, { ...f, note: pin.note });
  }

  if (fix) {
    for (const [rel, findings] of byFile) {
      const abs = path.join(REPO_ROOT, rel);
      if (!fs.existsSync(abs)) continue;
      let text = fs.readFileSync(abs, "utf8");
      for (const f of findings) {
        if (f.found.startsWith("(")) continue;
        text = applyFix(text, f);
      }
      fs.writeFileSync(abs, text);
      console.log(`fixed ${rel} (${findings.length})`);
    }
    return 0;
  }

  const total = [...byFile.values()].reduce((n, f) => n + f.length, 0);
  if (total > 0) {
    console.error(`release-refs: ${total} reference(s) do not name v${version}\n`);
    for (const [rel, findings] of byFile) {
      for (const f of findings) {
        const where = f.line ? `${rel}:${f.line}` : rel;
        const why = f.note ? `  (${f.note})` : "";
        console.error(`  ${where}  ${f.kind}  ${f.found} -> ${f.want}${why}`);
      }
    }
    console.error(`\nRun \`node tools/release-refs/lint.mjs --fix\` to update them.`);
    return 1;
  }

  console.log(`release-refs: every reference names v${version}`);
  for (const p of config.pending ?? []) {
    console.log(`  not yet enrolled: ${p.file} (${p.blocked_by})`);
  }
  return 0;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}
