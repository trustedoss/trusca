#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Entry-chunk size budget (#421).
 *
 * `frontend-bundle-audit` (the CI job this runs in) checked for three
 * specific string leaks but never a size regression, despite its name. The
 * entry chunk (`dist/assets/index-*.js`, Vite's default name for the module
 * graph rooted at `index.html`) was 1,828.77 kB raw / 502.48 kB gzip before
 * #421 moved every route except the four auth pages and the dashboard to
 * `React.lazy()`; after, it is ~950 kB raw / ~295 kB gzip. This holds that
 * number down: the whole point of splitting the routes out was that a
 * regression (a heavy import added back to one of the still-eager pages, or
 * to something they transitively pull in) should fail loudly here rather
 * than silently regrowing the entry chunk one import at a time.
 *
 * Budget is on the GZIPPED size, because that is what actually crosses the
 * network, and raw minified size does not reflect what a visitor waits on.
 * 400 KB gzip: measured floor was ~295 KB; this leaves headroom for
 * legitimate growth (the eager pages are real product surface, not just
 * vendor code) without being loose enough to miss the kind of regression
 * this exists to catch, matching this repo's "capture the real floor, set
 * the threshold with headroom over it" convention for baseline-derived
 * gates (see `tools/em-dash` and the visual-gate baselines for the same
 * shape of decision elsewhere in this codebase).
 *
 * CSS budget (Primer migration, stage 2)
 * --------------------------------------
 * The same script also holds the total gzip size of `dist/assets/*.css`. The
 * Primer theme files (@primer/primitives, light and dark) are loaded as plain
 * CSS and every Primer component adds its own stylesheet when a screen starts
 * using it, so the number only goes one way during the migration unless it is
 * watched.
 *
 * Measured 2026-11-14 on the foundation PR: 40.2 KB gzip (240.7 KB raw),
 * against 11.0 KB gzip before the Primer packages. Entry JS was 295.7 KB gzip
 * against 294.3 KB before (the script's own measure, level 9). Rounding the
 * measured value up to the next 2 KB gives 42 KB, but the plan sets a floor of
 * 60 KB, because a spike with the first components in place measured 55.9 KB
 * and the budget has to leave room for them; so the budget is 60 KB.
 */
import { gzipSync } from "node:zlib";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const DIST_DIR = fileURLToPath(new URL("../dist/", import.meta.url));
const BUDGET_GZIP_BYTES = 400 * 1024;
const CSS_BUDGET_GZIP_BYTES = 60 * 1024;

/** Total gzip size of every stylesheet Vite emitted. */
function measureCss() {
  const dir = join(DIST_DIR, "assets");
  let files;
  try {
    files = readdirSync(dir).filter((f) => f.endsWith(".css"));
  } catch (err) {
    console.error(`bundle-size-budget: could not read ${dir} (${err.message}). Run \`npm run build\` first.`);
    process.exit(1);
  }
  if (files.length === 0) {
    // A build with no CSS means the measurement, not the CSS, is broken.
    console.error("bundle-size-budget: dist/assets has no .css files; Vite's output shape changed.");
    process.exit(1);
  }
  let gzip = 0;
  let rawBytes = 0;
  for (const f of files) {
    const buf = readFileSync(join(dir, f));
    rawBytes += buf.length;
    gzip += gzipSync(buf, { level: 9 }).length;
  }
  return { files: files.length, gzip, rawBytes };
}

function findEntryChunk() {
  // Read the answer off `dist/index.html`'s own `<script type="module">`
  // tag rather than globbing `dist/assets/index-*.js`: Vite's chunk-naming
  // heuristic sometimes names an UNRELATED shared chunk "index-<hash>.js"
  // too (a chunk pulled in from more than one place with no single obvious
  // owner name), which made the glob approach find two "index" chunks and
  // have no principled way to pick between them. The HTML is the one place
  // that says, unambiguously, which chunk the browser actually loads first.
  let html;
  try {
    html = readFileSync(join(DIST_DIR, "index.html"), "utf-8");
  } catch (err) {
    console.error(
      `bundle-size-budget: could not read ${DIST_DIR}index.html (${err.message}). ` +
        "Run `npm run build` first.",
    );
    process.exit(1);
  }
  const match = html.match(/<script[^>]*\stype="module"[^>]*\ssrc="([^"]+)"/);
  if (!match) {
    console.error(
      "bundle-size-budget: dist/index.html has no <script type=\"module\" src=...> " +
        "tag. Vite's output shape changed; update this script's assumption.",
    );
    process.exit(1);
  }
  // The src is an absolute path from the site root (e.g. "/assets/index-
  // B0zo_VGF.js"); dist/ IS that root once built, so strip the leading "/".
  return join(DIST_DIR, match[1].replace(/^\//, ""));
}

const entryPath = findEntryChunk();
const raw = readFileSync(entryPath);
const gzipBytes = gzipSync(raw, { level: 9 }).length;
const rawKB = (raw.length / 1024).toFixed(1);
const gzipKB = (gzipBytes / 1024).toFixed(1);
const budgetKB = (BUDGET_GZIP_BYTES / 1024).toFixed(0);

if (gzipBytes > BUDGET_GZIP_BYTES) {
  console.error(
    `bundle-size-budget: entry chunk is ${gzipKB} KB gzip (${rawKB} KB raw), ` +
      `over the ${budgetKB} KB gzip budget.\n` +
      "Something eager (one of the four auth pages, the dashboard, or " +
      "something they import) grew, or a new page was added to router.tsx " +
      "without React.lazy(). Either move it behind React.lazy() or, if the " +
      "growth is legitimate, raise BUDGET_GZIP_BYTES here with the same " +
      "reasoning this file's docstring uses.",
  );
  process.exit(1);
}

const css = measureCss();
const cssGzipKB = (css.gzip / 1024).toFixed(1);
const cssBudgetKB = (CSS_BUDGET_GZIP_BYTES / 1024).toFixed(0);
if (css.gzip > CSS_BUDGET_GZIP_BYTES) {
  console.error(
    `bundle-size-budget: CSS is ${cssGzipKB} KB gzip across ${css.files} file(s) ` +
      `(${(css.rawBytes / 1024).toFixed(1)} KB raw), over the ${cssBudgetKB} KB gzip budget.\n` +
      "A Primer theme file or component stylesheet was added, or a screen now imports " +
      "more of them than planned. Remove the import, or raise CSS_BUDGET_GZIP_BYTES " +
      "with the measurement and reasoning in this file's docstring.",
  );
  process.exit(1);
}

console.log(
  `bundle-size-budget: OK, CSS ${cssGzipKB} KB gzip, budget ${cssBudgetKB} KB gzip.`,
);
console.log(
  `bundle-size-budget: OK, entry chunk ${gzipKB} KB gzip (${rawKB} KB raw), ` +
    `budget ${budgetKB} KB gzip.`,
);
