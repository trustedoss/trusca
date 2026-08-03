#!/usr/bin/env node
/**
 * G0-1 — design-token ratchet lint.
 *
 * Why this exists
 * ---------------
 * Colour discipline was the one design rule no machine enforced, and it
 * decayed exactly as you would expect: 158 token bypasses accumulated
 * across the app (raw `#rrggbb` literals plus Tailwind palette classes
 * like `bg-amber-50` / `text-emerald-700`), with the same semantic state
 * rendered in a different shade depending on which file you opened.
 *
 * A plain "ban raw colours" gate would have been unshippable — a chunk of
 * those bypasses existed because the token system had no answer for them
 * (there was no readable text token for a risk tint, and no status surface
 * family at all). Those tokens now exist, so the ban is implementable.
 *
 * The ratchet
 * -----------
 * Rather than block the branch on 158 pre-existing violations, the current
 * count is frozen in `token-lint-baseline.json` and the gate enforces a
 * monotone rule:
 *
 *   - a violation in a file that has no budget → fail (new debt)
 *   - a file over its recorded budget         → fail (grew)
 *   - a file under its recorded budget        → fail, asking you to commit
 *     the lowered baseline (debt that is paid stays paid)
 *
 * That last rule is what makes it a ratchet rather than a cap: the number
 * can only travel in one direction, and the plan (differentiation Waves
 * W14–W17) drives it to zero.
 *
 * Usage
 *   node scripts/token-lint.mjs             # check (CI)
 *   node scripts/token-lint.mjs --update    # re-record the baseline
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..");
const SRC_ROOT = path.join(FRONTEND_ROOT, "src");
const BASELINE_PATH = path.join(__dirname, "token-lint-baseline.json");

/**
 * Tailwind palette families that carry colour. Semantic scales the design
 * system owns (`risk-*`, `status-*`, `primary`, `muted`, …) are absent by
 * construction — they are not palette names, so they never match.
 */
const PALETTE = [
  "red",
  "orange",
  "amber",
  "yellow",
  "lime",
  "green",
  "emerald",
  "teal",
  "cyan",
  "sky",
  "blue",
  "indigo",
  "violet",
  "purple",
  "fuchsia",
  "pink",
  "rose",
  "slate",
  "gray",
  "zinc",
  "neutral",
  "stone",
];

const UTILITY = [
  "bg",
  "text",
  "border",
  "ring",
  "fill",
  "stroke",
  "from",
  "via",
  "to",
  "decoration",
  "outline",
  "shadow",
  "accent",
  "caret",
  "divide",
  "placeholder",
];

/** `bg-amber-50`, `dark:text-emerald-700/80`, `hover:border-red-300` … */
const PALETTE_CLASS = new RegExp(
  String.raw`\b(?:${UTILITY.join("|")})-(?:${PALETTE.join("|")})-\d{2,3}(?:/\d{1,3})?\b`,
  "g",
);

/** `#fff`, `#18181b`, `#18181bcc` — literal colours outside the token files. */
const RAW_HEX = /#[0-9a-fA-F]{3,8}\b/g;

/**
 * Files that legitimately hold raw colour values.
 *
 * `index.css` and `tailwind.config.ts` are where tokens are *defined*.
 * Brand marks are fixed-palette SVG artwork (brand-trusca.md §4), not
 * themeable surfaces. Test files assert on literals by nature.
 */
const EXEMPT = [
  /^src\/index\.css$/,
  /^src\/components\/Brand(Mark|Wordmark|Lockup)\.tsx$/,
  // Third-party brand marks (Google "G", GitHub, …). Their colours are
  // dictated by the vendor's brand guidelines and must NOT follow our
  // theme — same reasoning as our own BrandMark.
  /^src\/components\/ProviderIcon\.tsx$/,
  /\.test\.(ts|tsx)$/,
  /\.spec\.(ts|tsx)$/,
];

const SCANNED_EXTENSIONS = new Set([".ts", ".tsx", ".css"]);

function isExempt(relPath) {
  return EXEMPT.some((rx) => rx.test(relPath));
}

/**
 * Blank out `//` and `/* … *\/` comments, preserving every newline so line
 * numbers survive. Block comments are handled across lines — the JSDoc
 * headers in this codebase routinely cite issue numbers (`W6-#43e`) that
 * would otherwise read as three-digit hex.
 */
export function stripComments(source) {
  const blank = (match) => match.replace(/[^\n]/g, " ");
  return source
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/\/\/[^\n]*/g, blank);
}

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      walk(full, out);
    } else if (SCANNED_EXTENSIONS.has(path.extname(entry.name))) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Count token bypasses per file.
 *
 * @returns {{counts: Record<string, number>, findings: Array<{file: string, line: number, text: string}>}}
 */
export function scan(root = SRC_ROOT, frontendRoot = FRONTEND_ROOT) {
  const counts = {};
  const findings = [];

  for (const absolute of walk(root)) {
    const rel = path.relative(frontendRoot, absolute).split(path.sep).join("/");
    if (isExempt(rel)) continue;

    // Comments explain colour choices constantly ("was #fafafa navy") and
    // cite issues that look like short hex (`W6-#43e`, `chore #365`).
    // Linting prose would train people to phrase comments around the
    // linter, so comments are blanked before matching — blanked in place,
    // so reported line numbers still point at real source lines.
    const lines = stripComments(fs.readFileSync(absolute, "utf8")).split("\n");
    lines.forEach((code, index) => {
      const hits = [
        ...(code.match(PALETTE_CLASS) ?? []),
        ...(code.match(RAW_HEX) ?? []),
      ];
      for (const text of hits) {
        counts[rel] = (counts[rel] ?? 0) + 1;
        findings.push({ file: rel, line: index + 1, text });
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
    const total = Object.values(sorted).reduce((a, b) => a + b, 0);
    console.log(
      `token-lint: baseline updated — ${total} bypasses across ${Object.keys(sorted).length} files`,
    );
    return 0;
  }

  if (!fs.existsSync(BASELINE_PATH)) {
    console.error(
      "token-lint: no baseline found. Run `node scripts/token-lint.mjs --update` once and commit it.",
    );
    return 1;
  }

  const baseline = JSON.parse(fs.readFileSync(BASELINE_PATH, "utf8"));
  const result = diff(counts, baseline);

  if (result.ok) {
    console.log(
      `token-lint: OK — ${result.total} known bypasses, none added (budget ${result.baselineTotal}).`,
    );
    return 0;
  }

  const byFile = (file) => findings.filter((f) => f.file === file);

  if (result.added.length > 0) {
    console.error("\ntoken-lint: NEW token bypasses — use a design token.\n");
    for (const { file } of result.added) {
      for (const f of byFile(file)) {
        console.error(`  ${f.file}:${f.line}  ${f.text}`);
      }
    }
  }

  if (result.grew.length > 0) {
    console.error("\ntoken-lint: bypasses INCREASED in these files.\n");
    for (const { file, count, budget } of result.grew) {
      console.error(`  ${file}  ${budget} → ${count}`);
      for (const f of byFile(file)) {
        console.error(`      line ${f.line}  ${f.text}`);
      }
    }
  }

  if (result.shrank.length > 0) {
    console.error(
      "\ntoken-lint: bypasses DECREASED — commit the lowered baseline so the",
    );
    console.error("            gain is locked in:\n");
    console.error("              node scripts/token-lint.mjs --update\n");
    for (const { file, count, budget } of result.shrank) {
      console.error(`  ${file}  ${budget} → ${count}`);
    }
  }

  console.error(
    `\nAvailable tokens: status-{success,warning,danger,info}{,-subtle,-border,-foreground},`,
  );
  console.error(
    "                  risk-{critical,high,medium,low,info}{,-foreground},",
  );
  console.error(
    "                  plus the shadcn semantic set (muted, destructive, …).",
  );
  console.error("See src/index.css for the contract.\n");

  return 1;
}

// Only run when invoked directly — the unit test imports `scan` / `diff`.
if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  process.exit(main());
}
