#!/usr/bin/env node
/**
 * em-dash: keeps the em dash (U+2014) out of newly written source text.
 *
 * House style forbids it in anything we author: comments, commit messages,
 * documentation, Korean product copy. Hyphens, commas, parentheses and full
 * stops carry the same meaning without the character.
 *
 * The check is diff-based rather than a per-file ratchet, because the rule is
 * about what gets ADDED. A ratchet would need a baseline listing 1,318 files
 * and 11,271 existing occurrences, and would still say nothing about the line
 * in front of you. Comparing against the merge base says exactly the thing
 * the rule says.
 *
 * Why a script at all, for one character: self-review missed it for eight
 * consecutive units of work. A machine reads punctuation better than an
 * author re-reading their own paragraph.
 *
 * Exempt, because neither is prose:
 *   - a string whose entire value is the character, used as the "no value"
 *     placeholder in tables (e.g. "no_detail": "—")
 *   - a line carrying an `em-dash-allow: <reason>` marker, on the line or the
 *     one above it, with a reason of four words or more
 *
 * Pure Node ESM, no dependencies, same shape as tools/ko-style/lint.mjs.
 *
 * Usage:
 *   node tools/em-dash/lint.mjs                     # added lines vs origin/main
 *   node tools/em-dash/lint.mjs --base <ref>        # against another base
 *   node tools/em-dash/lint.mjs --staged            # what is about to commit
 *   node tools/em-dash/lint.mjs --files a.ts b.py   # whole files, every line
 *   node tools/em-dash/lint.mjs --all               # repo-wide census (advisory)
 */
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(__dirname, "..", "..");

export const EM_DASH = "—";

/**
 * Extensions whose contents we author as prose: code (comments), config
 * (comments), and the locale catalogues (product copy). Lock files, snapshots
 * and generated output are not in the list because nobody writes a sentence
 * in them.
 */
const WATCHED = new Set([
  ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
  ".py", ".sh", ".sql",
  ".yml", ".yaml", ".json", ".css", ".html",
  ".md", ".mdx",
]);

/** Paths that are someone else's text, or ours but frozen deliberately. */
const SKIP_PREFIXES = [
  "apps/frontend/tests/visual/visual.spec.ts-snapshots/",
  "docs-site/i18n/",
  "THIRD_PARTY_NOTICES",
];

export function isWatched(file) {
  if (SKIP_PREFIXES.some((p) => file.startsWith(p) || file.includes(`/${p}`))) {
    return false;
  }
  return WATCHED.has(path.extname(file));
}

/**
 * A value that is nothing but the character, with optional surrounding
 * quotes, is a placeholder glyph rather than punctuation inside a sentence.
 * `"no_error": "—"` is the shape; `"a — b"` is not.
 */
export function isPlaceholderOnly(line) {
  const stripped = line.trim();
  // JSON / JS property whose whole value is the character.
  if (/:\s*["'`]\s*—\s*["'`],?$/.test(stripped)) return true;
  // A bare quoted character on its own line (JSX prop, array member, …).
  if (/^["'`]\s*—\s*["'`],?$/.test(stripped)) return true;
  return false;
}

const ALLOW_MARKER = /em-dash-allow:\s*(.+)$/;

export function allowReason(line) {
  const match = ALLOW_MARKER.exec(line);
  if (!match) return null;
  const reason = match[1].replace(/\*\/\s*$/, "").trim();
  return reason.split(/\s+/).filter(Boolean).length >= 4 ? reason : null;
}

function git(args) {
  return execFileSync("git", args, {
    cwd: REPO_ROOT,
    encoding: "utf8",
    maxBuffer: 128 * 1024 * 1024,
  });
}

/**
 * Refuse to run against a base the clone does not have.
 *
 * CI checks out shallow, so `origin/main` is often absent and `git diff` then
 * produces nothing. A diff-based gate that finds nothing to read reports
 * success, which is indistinguishable from a clean branch. This repository has
 * already shipped one gate that passed because it examined nothing (see the
 * ko-style step in ci.yml); the workflow fetches the base before calling this,
 * and if that ever stops working the run must fail rather than congratulate us.
 */
function requireBase(base) {
  try {
    git(["rev-parse", "--verify", `${base}^{commit}`]);
    return true;
  } catch {
    console.error(
      `em-dash: cannot resolve base ref '${base}'. A shallow clone needs\n` +
        `  git fetch --no-tags --depth=1 origin <base-branch>\n` +
        `before this runs. Refusing to report success on a diff of nothing.`,
    );
    return false;
  }
}

/**
 * Added lines per file, from a unified diff. Returns
 * `[{file, line, text}, …]` where `line` is the line number in the new file.
 */
export function addedLines(diff) {
  const out = [];
  let file = null;
  let lineNo = 0;
  for (const raw of diff.split("\n")) {
    if (raw.startsWith("+++ b/")) {
      file = raw.slice(6);
      continue;
    }
    if (raw.startsWith("@@")) {
      const m = /\+(\d+)/.exec(raw);
      lineNo = m ? Number(m[1]) : 0;
      continue;
    }
    if (!file) continue;
    if (raw.startsWith("+") && !raw.startsWith("+++")) {
      out.push({ file, line: lineNo, text: raw.slice(1) });
      lineNo += 1;
    } else if (!raw.startsWith("-") && !raw.startsWith("\\")) {
      lineNo += 1;
    }
  }
  return out;
}

/** Keep only the candidates that actually break the rule. */
export function findings(candidates, contextFor) {
  const out = [];
  for (const c of candidates) {
    if (!c.text.includes(EM_DASH)) continue;
    if (!isWatched(c.file)) continue;
    if (isPlaceholderOnly(c.text)) continue;
    if (allowReason(c.text)) continue;
    const above = contextFor ? contextFor(c) : null;
    if (above && allowReason(above)) continue;
    out.push(c);
  }
  return out;
}

function parseArgs(argv) {
  const opts = { mode: "diff", base: "origin/main", files: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--all") opts.mode = "all";
    else if (arg === "--staged") opts.mode = "staged";
    else if (arg === "--files") {
      opts.mode = "files";
      while (argv[i + 1] && !argv[i + 1].startsWith("--")) {
        opts.files.push(argv[(i += 1)]);
      }
    } else if (arg === "--base") opts.base = argv[(i += 1)];
  }
  return opts;
}

function readLines(file) {
  const full = path.join(REPO_ROOT, file);
  if (!fs.existsSync(full)) return [];
  return fs.readFileSync(full, "utf8").split("\n");
}

function main() {
  const opts = parseArgs(process.argv.slice(2));

  if (opts.mode === "all") {
    // Census only. Prints what the repo still carries so the debt is visible
    // without blocking anything on it.
    const files = git(["ls-files", "-z"]).split("\0").filter(Boolean);
    let total = 0;
    let hitFiles = 0;
    for (const file of files) {
      if (!isWatched(file)) continue;
      const hits = readLines(file).filter(
        (l) => l.includes(EM_DASH) && !isPlaceholderOnly(l),
      ).length;
      if (hits) {
        total += hits;
        hitFiles += 1;
      }
    }
    console.log(
      `em-dash: census only, ${total} line(s) across ${hitFiles} file(s). ` +
        `Pre-existing text is not a gate; new lines are.`,
    );
    return 0;
  }

  let candidates;
  if (opts.mode === "files") {
    candidates = opts.files.flatMap((file) =>
      readLines(file).map((text, i) => ({ file, line: i + 1, text })),
    );
  } else if (opts.mode === "staged") {
    candidates = addedLines(
      git(["diff", "--cached", "--unified=0", "--no-color"]),
    );
  } else {
    if (!requireBase(opts.base)) return 1;
    // Merge base, then a two-dot diff against it, so the working tree counts.
    // `base...HEAD` reads committed history only, which means running this
    // before committing reports success on the very line you just typed. That
    // is the moment the check is most useful. (Verified by adding an em dash
    // to a comment and watching the three-dot form pass.)
    const mergeBase = git(["merge-base", opts.base, "HEAD"]).trim();
    candidates = addedLines(
      git(["diff", mergeBase, "--unified=0", "--no-color"]),
    );
  }

  const hits = findings(candidates, (c) => {
    const lines = readLines(c.file);
    return lines[c.line - 2] ?? null;
  });

  if (hits.length === 0) {
    const scope =
      opts.mode === "files"
        ? `${opts.files.length} file(s)`
        : opts.mode === "staged"
          ? "staged changes"
          : `changes since ${opts.base}`;
    console.log(`em-dash: OK, no em dash added in ${scope}.`);
    return 0;
  }

  console.error(
    `em-dash: ${hits.length} line(s) use the em dash (U+2014). Use a hyphen, ` +
      `a comma, parentheses, or split the sentence.\n`,
  );
  for (const hit of hits) {
    console.error(`  ${hit.file}:${hit.line}`);
    console.error(`    ${hit.text.trim()}`);
  }
  console.error(
    `\nIf a line genuinely needs the character, put ` +
      `\`em-dash-allow: <reason of four words or more>\` on it or the line above.`,
  );
  return 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}
