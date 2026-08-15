#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Ratchet on error copy that bypasses translation.
 *
 * The backend answers RFC 7807 with an English `detail`, always populated.
 * Reading it straight into the UI is one short expression, so it spread: by
 * the time this gate was written, call sites across the feature tree rendered
 * the backend's English instead of the translation key sitting right next to
 * it. Every one of them looked reasonable alone. Nothing counted them.
 *
 * So this counts them, per file, against a recorded baseline that may only go
 * down: the same shape as `token-lint.mjs`, for the same reason: a debt that
 * nothing measures is a debt that grows. New bypasses fail; paid-down debt
 * must be re-recorded so the budget cannot be quietly re-spent.
 *
 * What counts as a bypass: reading `.detail` or `.title` off a caught error or
 * a query's error, in source (not comments). Both fields are always English.
 * What does not count: `lib/problemMessage.ts` and the domain mappers, which
 * are the sanctioned path; a `detail` field belonging to a domain payload
 * rather than a Problem (the health panel's component detail, a bulk
 * operation's per-item reason), which the receiver list below excludes.
 *
 * Usage:
 *   node scripts/problem-detail-lint.mjs             # check (CI)
 *   node scripts/problem-detail-lint.mjs --update    # re-record the baseline
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(__dirname, "..");
const SRC_ROOT = path.join(FRONTEND_ROOT, "src");
const BASELINE_PATH = path.join(__dirname, "problem-detail-baseline.json");

/**
 * Files where `.detail` is the sanctioned path or belongs to something other
 * than an RFC 7807 problem. Exact paths, not patterns: a pattern here would
 * quietly exempt files nobody reviewed.
 */
const EXEMPT = new Set([
  // The Problem type and the helpers that localize it.
  "src/lib/problem.ts",
  "src/lib/problemMessage.ts",
  "src/lib/demoReadOnly.ts",
  "src/features/admin/lib/adminErrorMessage.ts",
  "src/features/projects/lib/projectErrorMessage.ts",
]);

/**
 * Both `detail` and `title` are always-English fields on the Problem, and both
 * get rendered. Watching only `detail` would leave `title` as an open door,
 * and there are already four call sites drawing it.
 */
const ENGLISH_FIELD = "(?:detail|title)";

/**
 * `<error>.detail` where the receiver names an error. Deliberately narrow: it
 * matches the receivers this codebase actually uses, so a domain payload with
 * a `detail` field does not read as a bypass. Widening the receiver list is
 * fine; widening it to a bare field name is not, and would flag unrelated
 * payloads. `?.` and `!.` are included because they are one character away
 * from the plain form and would otherwise slip through unintentionally.
 */
const ERROR_FIELD = new RegExp(
  String.raw`\b(?:err|error|e|ex|problem|caught|reason|cause|mutationError|createErr|deleteErr|updateErr|[a-zA-Z]*[Ee]rror)\s*[?!]?\s*\.\s*` +
    ENGLISH_FIELD +
    String.raw`\b`,
  "g",
);

/**
 * The same fields read through a cast, e.g. `(err as ProblemError).detail`.
 * The receiver-name rule above cannot see these, the token before the dot is
 * the cast's closing paren, and a cast is exactly what someone reaches for
 * when the plain form is flagged.
 */
const CAST_FIELD = new RegExp(
  String.raw`\bas\s+(?:any|unknown|ProblemError|ProblemDetails)\b[^\n)]*\)\s*[?!]?\s*\.\s*` +
    ENGLISH_FIELD +
    String.raw`\b`,
  "g",
);

/** Blank comments in place so line numbers stay put. */
export function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, lead) =>
      lead + " ".repeat(match.length - lead.length),
    );
}

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) yield* walk(full);
    else if (/\.tsx?$/.test(entry.name)) yield full;
  }
}

/**
 * Count bypasses per file.
 *
 * @returns {{counts: Record<string, number>, findings: Array<{file: string, line: number, text: string}>}}
 */
export function scan(root = SRC_ROOT, frontendRoot = FRONTEND_ROOT) {
  const counts = {};
  const findings = [];

  for (const absolute of walk(root)) {
    const rel = path.relative(frontendRoot, absolute).split(path.sep).join("/");
    if (EXEMPT.has(rel)) continue;

    const raw = fs.readFileSync(absolute, "utf8").split("\n");
    const lines = stripComments(raw.join("\n")).split("\n");
    lines.forEach((code, index) => {
      // A line-level opt-out, for the places where the server's text really is
      // better than anything we can translate: a 422 governance message naming
      // the limit that was exceeded, or a `detail` that belongs to an uploaded
      // document rather than to a Problem. The reason is required so the next
      // reader can judge it; a bare marker does not count.
      //
      // Accepted on the line itself or the one above, so a long expression
      // does not have to grow a trailing comment to be exempted.
      // At least a few words of reason, not one character: a marker that
      // accepts "x" is a marker that will be used with "x".
      const marker = /problem-detail-lint-allow:\s*\S+(?:\s+\S+){3,}/;
      if (marker.test(raw[index] ?? "") || marker.test(raw[index - 1] ?? "")) {
        return;
      }
      const hits = [
        ...(code.match(ERROR_FIELD) ?? []),
        ...(code.match(CAST_FIELD) ?? []),
      ];
      for (const text of hits) {
        counts[rel] = (counts[rel] ?? 0) + 1;
        findings.push({ file: rel, line: index + 1, text: text.trim() });
      }
    });
  }

  return { counts, findings };
}

/**
 * Compare a fresh scan against the recorded baseline.
 *
 * @returns {{ok: boolean, added: Array, grew: Array, shrank: Array, total: number, baselineTotal: number}}
 */
export function diff(counts, baseline) {
  const added = [];
  const grew = [];
  const shrank = [];

  for (const [file, count] of Object.entries(counts)) {
    const budget = baseline[file] ?? 0;
    if (budget === 0) added.push({ file, count });
    else if (count > budget) grew.push({ file, count, budget });
    else if (count < budget) shrank.push({ file, count, budget });
  }
  for (const [file, budget] of Object.entries(baseline)) {
    if (!(file in counts)) shrank.push({ file, count: 0, budget });
  }

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const baselineTotal = Object.values(baseline).reduce((a, b) => a + b, 0);

  return {
    ok: added.length === 0 && grew.length === 0 && shrank.length === 0,
    added,
    grew,
    shrank,
    total,
    baselineTotal,
  };
}

function main() {
  const update = process.argv.includes("--update");
  const { counts, findings } = scan();

  if (update) {
    const sorted = Object.fromEntries(
      Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)),
    );
    fs.writeFileSync(BASELINE_PATH, `${JSON.stringify(sorted, null, 2)}\n`);
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    console.log(
      `problem-detail-lint: recorded ${total} bypass(es) across ` +
        `${Object.keys(counts).length} file(s).`,
    );
    return 0;
  }

  if (!fs.existsSync(BASELINE_PATH)) {
    console.error(
      "problem-detail-lint: no baseline found. Run " +
        "`node scripts/problem-detail-lint.mjs --update` once and commit it.",
    );
    return 1;
  }

  const baseline = JSON.parse(fs.readFileSync(BASELINE_PATH, "utf8"));
  const result = diff(counts, baseline);

  if (result.ok) {
    console.log(
      `problem-detail-lint: OK — ${result.total} bypass(es), matching the baseline.`,
    );
    return 0;
  }

  if (result.added.length || result.grew.length) {
    console.error(
      "problem-detail-lint: new error copy bypasses translation.\n" +
        "The backend's RFC 7807 `detail` is always English. Route the error\n" +
        "through `problemMessage()` (src/lib/problemMessage.ts), or through a\n" +
        "domain mapper if the surface classifies extension fields.\n",
    );
    for (const { file, count } of result.added) {
      console.error(`  + ${file}: ${count} (was 0)`);
      for (const f of findings.filter((f) => f.file === file)) {
        console.error(`      ${f.file}:${f.line}  ${f.text}`);
      }
    }
    for (const { file, count, budget } of result.grew) {
      console.error(`  ↑ ${file}: ${count} (budget ${budget})`);
    }
  }

  if (result.shrank.length) {
    console.error(
      "\nproblem-detail-lint: debt was paid down but the baseline still\n" +
        "budgets for it. Re-record so the budget cannot be re-spent:\n" +
        "    node scripts/problem-detail-lint.mjs --update\n",
    );
    for (const { file, count, budget } of result.shrank) {
      console.error(`  ↓ ${file}: ${count} (budget ${budget})`);
    }
  }

  console.error(
    `\ntotal ${result.total}, baseline ${result.baselineTotal}.`,
  );
  return 1;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.exit(main());
}
