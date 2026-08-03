#!/usr/bin/env node
/**
 * license-header — SPDX header gate for first-party source files.
 *
 * Apache-2.0 does not require per-file headers; its appendix recommends them.
 * We want them for two concrete reasons:
 *
 *   1. TRUSCA is a tool that detects licenses per file. Scanning our own
 *      repository with our own product and getting NOASSERTION on every source
 *      file is a claim about the product, not just about the repo.
 *   2. Vendored third-party files sit in the same directories as first-party
 *      ones (services/g7_registry.json next to services/g7_conformance.py).
 *      A per-file marker is what distinguishes them at a glance.
 *
 * Usage:
 *   node tools/license-header/lint.mjs --all              # check everything
 *   node tools/license-header/lint.mjs --all --fix        # insert what's missing
 *   node tools/license-header/lint.mjs --changed          # check vs. merge-base
 *   node tools/license-header/lint.mjs <file> [<file>...]  # check named files
 *
 * Exit codes: 0 = clean (or --fix succeeded), 1 = files missing a header.
 *
 * Checking and inserting live in ONE tool on purpose. Split across two, they
 * drift into a state where the inserter writes what the gate rejects.
 */
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(__dirname, "..", "..");

export const SPDX_ID = "Apache-2.0";
export const COPYRIGHT_HOLDER = "TRUSCA contributors";

/** The year a NEW header gets stamped with. Existing years are left alone. */
const CURRENT_YEAR = "2026";

/**
 * Paths scanned in `--all` mode — directories or individual files.
 *
 * Beyond the two application trees, this covers the parts of the repository an
 * operator or a CI user receives as INDIVIDUAL files rather than inside an
 * image: the install / backup / restore wrappers they run on a host, the GitHub
 * Action their workflow references by path, the compose files that are the
 * on-premise deployment unit, and the Helm chart. For those the argument for a
 * per-file header is stronger than for application code, not weaker — someone
 * can hold exactly one of them and nothing else.
 *
 * Tests are excluded: they are not distributed in any artifact, and the reason
 * for per-file headers (a recipient of one file can tell its license) does not
 * apply to code that never leaves the repository. Revisit if we ever publish a
 * test-fixture package.
 *
 * Also out: `deploy/` (our own demo-host provisioning, not a product artifact),
 * `tools/` (developer tooling, never shipped), `docs-site/`, and `.github/`.
 */
const SCAN_ROOTS = [
  "apps/backend",
  "apps/frontend/src",
  "scripts",
  "actions",
  "charts",
  "docker-compose.yml",
  "docker-compose.dev.yml",
  "docker-compose.demo.yml",
  "docker-compose.smoke.yml",
];
const EXCLUDED_DIR_SEGMENTS = new Set([
  "tests",
  "node_modules",
  "__pycache__",
  ".venv",
  ".mypy_cache",
  ".pytest_cache",
  ".ruff_cache",
  "dist",
  "coverage",
]);

/** extension → line-comment prefix. Extensions absent here are not gated. */
const COMMENT_PREFIX = {
  ".py": "#",
  ".ts": "//",
  ".tsx": "//",
  ".sh": "#",
  ".yml": "#",
  ".yaml": "#",
  ".tpl": "#", // overridden for Helm templates — see HELM_TEMPLATE_RE
};

/**
 * Helm templates need a HELM comment, not a YAML one.
 *
 * A `#` comment survives rendering and lands in the manifest the operator
 * applies. A Helm template comment is stripped by the template engine, so the
 * rendered output stays byte-identical to what it was before the header. Nine
 * templates already open with one, so this matches the chart's convention.
 *
 * `templates/NOTES.txt` is deliberately NOT covered: it is text printed to the
 * operator after `helm install`, and `.txt` is not a gated extension anyway.
 */
const HELM_TEMPLATE_RE = /^charts\/[^/]+\/templates\/.+\.(?:yaml|yml|tpl)$/;

const HEADER_SCAN_LINES = 6;
const SPDX_RE = /SPDX-License-Identifier:\s*Apache-2\.0/;
const COPYRIGHT_RE = /Copyright\s+\d{4}(?:-\d{4})?\s+TRUSCA contributors/;

export function loadExcluded() {
  const raw = JSON.parse(
    fs.readFileSync(path.join(__dirname, "excluded.json"), "utf8"),
  );
  const paths = new Set();
  const globs = [];
  for (const [key, group] of Object.entries(raw)) {
    if (key.startsWith("$")) continue;
    for (const p of group.paths ?? []) paths.add(p);
    for (const g of group.globs ?? []) globs.push(globToRegExp(g));
  }
  return { paths, globs };
}

/** Minimal glob → RegExp: supports `**` (any depth) and `*` (one segment). */
function globToRegExp(glob) {
  const escaped = glob.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  // Split on the `**` forms BEFORE expanding single `*`, so the single-segment
  // rule cannot chew the output of the any-depth rule. No sentinels: they end up
  // as literal control characters in the source if you are not careful.
  const body = escaped
    .split("**/")
    .map((chunk) =>
      chunk
        .split("**")
        .map((part) => part.replace(/\*/g, "[^/]*"))
        .join(".*"),
    )
    .join("(?:.*/)?");
  return new RegExp(`^${body}$`);
}

export function isExcluded(rel, excluded = loadExcluded()) {
  if (excluded.paths.has(rel)) return true;
  return excluded.globs.some((re) => re.test(rel));
}

/**
 * Is this path in scope for the gate?
 *
 * Scope is deliberately narrow: a gated extension, under a scanned root, not in
 * an excluded directory, not in excluded.json, and not empty. Empty files
 * (`__init__.py` markers) get nothing — a copyright claim over zero bytes is
 * noise, and the gate would nag forever on a file nobody will ever fill in.
 */
export function isTargeted(rel, excluded = loadExcluded()) {
  if (!(path.extname(rel) in COMMENT_PREFIX)) return false;
  if (!SCAN_ROOTS.some((root) => rel === root || rel.startsWith(`${root}/`))) {
    return false;
  }
  if (rel.split("/").some((seg) => EXCLUDED_DIR_SEGMENTS.has(seg))) return false;
  if (isExcluded(rel, excluded)) return false;

  const abs = path.join(REPO_ROOT, rel);
  try {
    if (fs.statSync(abs).size === 0) return false;
  } catch {
    return false;
  }
  return true;
}

export function hasHeader(text) {
  const head = text.split("\n", HEADER_SCAN_LINES).join("\n");
  return SPDX_RE.test(head) && COPYRIGHT_RE.test(head);
}

export function headerFor(rel, year = CURRENT_YEAR) {
  const spdx = `SPDX-License-Identifier: ${SPDX_ID}`;
  const copyright = `Copyright ${year} ${COPYRIGHT_HOLDER}`;

  if (HELM_TEMPLATE_RE.test(rel)) {
    // Helm comment so the header never reaches the rendered manifest. The
    // leading `{{-` and trailing `-}}` also swallow the surrounding newlines,
    // which keeps `helm template` output identical to the pre-header bytes.
    return ["{{- /*", spdx, copyright, "*/ -}}"].join("\n");
  }

  const prefix = COMMENT_PREFIX[path.extname(rel)];
  return [`${prefix} ${spdx}`, `${prefix} ${copyright}`].join("\n");
}

/**
 * Return `text` with the header inserted, or `text` unchanged if already there.
 *
 * Insertion point is the top of the file, except after a shebang — a `#!` line
 * only works as line 1. Python module docstrings stay the first *statement*
 * (comments are not statements), so `from __future__ import` and docstring
 * semantics are unaffected; TS/TSX files take it above their JSDoc banner.
 */
export function withHeader(text, rel, year = CURRENT_YEAR) {
  if (hasHeader(text)) return text;
  const header = headerFor(rel, year);
  const lines = text.split("\n");
  if (lines[0]?.startsWith("#!")) {
    return [lines[0], header, ...lines.slice(1)].join("\n");
  }
  return `${header}\n${text}`;
}

function listTrackedFiles() {
  const out = execFileSync("git", ["ls-files", "-z", ...SCAN_ROOTS], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
  return out.split("\0").filter(Boolean);
}

function listChangedFiles() {
  const base = (() => {
    for (const ref of ["origin/main", "main"]) {
      try {
        return execFileSync("git", ["merge-base", "HEAD", ref], {
          cwd: REPO_ROOT,
          encoding: "utf8",
        }).trim();
      } catch {
        /* try the next ref */
      }
    }
    return null;
  })();
  if (!base) {
    console.error(
      "license-header: no merge-base against origin/main or main. In CI the " +
        "checkout is shallow — use --all there, as ci.yml does.",
    );
    process.exit(1);
  }
  const out = execFileSync(
    "git",
    ["diff", "--name-only", "--diff-filter=ACMR", "-z", base, "--", ...SCAN_ROOTS],
    { cwd: REPO_ROOT, encoding: "utf8", maxBuffer: 32 * 1024 * 1024 },
  );
  return out.split("\0").filter(Boolean);
}

function parseArgs(argv) {
  const opts = { all: false, changed: false, fix: false, files: [] };
  for (const arg of argv) {
    if (arg === "--all") opts.all = true;
    else if (arg === "--changed") opts.changed = true;
    else if (arg === "--fix") opts.fix = true;
    else if (arg.startsWith("--")) {
      console.error(`license-header: unknown flag ${arg}`);
      process.exit(2);
    } else opts.files.push(arg);
  }
  return opts;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const excluded = loadExcluded();

  let candidates;
  if (opts.all) candidates = listTrackedFiles();
  else if (opts.changed) candidates = listChangedFiles();
  else if (opts.files.length) {
    candidates = opts.files.map((f) =>
      path.relative(REPO_ROOT, path.resolve(process.cwd(), f)),
    );
  } else {
    console.error(
      "license-header: pass --all, --changed, or one or more file paths.",
    );
    process.exit(2);
  }

  const targeted = candidates.filter((rel) => isTargeted(rel, excluded));
  const missing = [];
  let fixed = 0;

  for (const rel of targeted) {
    const abs = path.join(REPO_ROOT, rel);
    const text = fs.readFileSync(abs, "utf8");
    if (hasHeader(text)) continue;
    if (opts.fix) {
      fs.writeFileSync(abs, withHeader(text, rel), "utf8");
      fixed += 1;
    } else {
      missing.push(rel);
    }
  }

  if (opts.fix) {
    console.log(
      `license-header: ${targeted.length} file(s) in scope, ${fixed} header(s) inserted.`,
    );
    process.exit(0);
  }

  if (missing.length) {
    console.error(
      `license-header: ${missing.length} of ${targeted.length} file(s) are missing the SPDX header:\n`,
    );
    for (const rel of missing) console.error(`  ${rel}`);
    console.error(
      "\nInsert them with:  node tools/license-header/lint.mjs --all --fix\n" +
        "If a file is third-party, do NOT stamp it — add it to " +
        "tools/license-header/excluded.json with a reason.",
    );
    process.exit(1);
  }

  console.log(
    `license-header: ${targeted.length} file(s) in scope, all headers present.`,
  );
  process.exit(0);
}

if (import.meta.url === `file://${process.argv[1]}`) main();
