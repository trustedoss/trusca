#!/usr/bin/env node
/* eslint-disable */
/**
 * i18n drift gate — chore A1.
 *
 * What this script does:
 *   1. Run `i18next-parser` against the source tree, writing the extracted
 *      JSON to a temporary directory (NOT the committed src/locales/).
 *   2. For each (locale, namespace) the parser emitted, compare the *key
 *      structure* against the committed file. We compare keys-only (not
 *      values) because EN holds English copy and KO holds Korean copy —
 *      values legitimately diverge.
 *   3. Enforce EN ↔ KO key parity: every key present in EN must also be
 *      present in KO, and vice versa. Untranslated keys leak through the
 *      UI as raw IDs; this is a release blocker per CLAUDE.md.
 *   4. Enforce plural-suffix hygiene (see checkPluralSuffixes below).
 *   5. Exit 0 on green, 1 on any drift with an actionable message.
 *
 * Run via:  npm run i18n:check
 * Fix via:  npm run i18n:extract  (then add the new keys to KO by hand)
 */
"use strict";

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const COMMITTED_LOCALES = path.join(ROOT, "src", "locales");
const LOCALES = ["en", "ko"];

function flatten(obj, prefix = "") {
  // Collect every leaf key path. We treat arrays as opaque (rare in this
  // codebase) so a list-shaped translation is one entry, not one per index.
  const keys = new Set();
  if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
    if (prefix) keys.add(prefix);
    return keys;
  }
  for (const [k, v] of Object.entries(obj)) {
    const path_ = prefix ? `${prefix}.${k}` : k;
    if (v !== null && typeof v === "object" && !Array.isArray(v)) {
      for (const inner of flatten(v, path_)) keys.add(inner);
    } else {
      keys.add(path_);
    }
  }
  return keys;
}

function readJson(file) {
  if (!fs.existsSync(file)) return {};
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (err) {
    console.error(`[i18n:check] failed to parse ${file}: ${err.message}`);
    process.exit(1);
  }
}

function listJsonRelative(rootDir, locale) {
  const dir = path.join(rootDir, locale);
  if (!fs.existsSync(dir)) return [];
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith(".json"))
    .map((name) => name.slice(0, -".json".length));
}

function setDiff(a, b) {
  // Returns elements in `a` but not in `b`, sorted for deterministic output.
  return [...a].filter((x) => !b.has(x)).sort();
}

// i18next 23.x resolves plurals with the Intl.PluralRules categories. `_plural`
// is the v3 spelling and is never looked up, so such a key is dead weight that
// silently degrades to the singular copy. That is how the build-gate reason for
// known-malicious packages shipped in the singular.
const PLURAL_CATEGORIES = ["zero", "one", "two", "few", "many", "other"];

/**
 * Plural-suffix hygiene. Two rules, both learned from live defects:
 *
 *   1. No `_plural` suffix. It is v3 syntax; v4 wants a CLDR category.
 *   2. Every `key_<category>` needs its bare `key` alongside it. Two reasons,
 *      and only the first applies to both locales: the parser runs with
 *      `pluralSeparator: false` so it only ever extracts the bare key, and
 *      without it check #1 above reports the call site as missing. Second,
 *      English resolves the bare key at count 1, because no `_one` variant
 *      is shipped. Korean never reaches the bare key at all: its single CLDR
 *      category is `other`, so KO resolves `_other` at every count and its
 *      bare string is kept for parity, not for reading.
 *
 * Returns a list of human-readable problems (empty when clean).
 */
function checkPluralSuffixes(locale, ns, keys) {
  const problems = [];
  for (const key of [...keys].sort()) {
    const leaf = key.slice(key.lastIndexOf(".") + 1);
    if (leaf.endsWith("_plural")) {
      problems.push(
        `  - ${locale}/${ns}.json: "${key}" uses the i18next v3 "_plural" suffix, ` +
          `which v4 never resolves. Rename it to "_other".`,
      );
      continue;
    }
    const category = PLURAL_CATEGORIES.find((c) => leaf.endsWith(`_${c}`));
    if (!category) continue;
    const base = key.slice(0, key.length - `_${category}`.length);
    if (!keys.has(base)) {
      problems.push(
        `  - ${locale}/${ns}.json: "${key}" has no bare "${base}" beside it. ` +
          `Add it: the parser only extracts the bare key, and EN resolves it at count 1.`,
      );
    }
  }
  return problems;
}

function main() {
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), "trustedoss-i18n-"));
  const tmpLocales = path.join(tmpRoot, "locales");
  fs.mkdirSync(tmpLocales, { recursive: true });

  // Run i18next-parser into the temp dir. We override `output` from the
  // config so the committed files are never touched.
  const cliBin = path.join(
    ROOT,
    "node_modules",
    ".bin",
    process.platform === "win32" ? "i18next.cmd" : "i18next",
  );
  if (!fs.existsSync(cliBin)) {
    console.error(
      "[i18n:check] i18next-parser is not installed. Run `npm ci` first.",
    );
    process.exit(1);
  }

  // Spawn the CLI directly (no shell) so the `$LOCALE` / `$NAMESPACE`
  // tokens are passed through as literals — bash would expand them to the
  // empty string and the parser would write `locales/.json`. Likewise the
  // `src/**/*.{ts,tsx}` glob is handled by i18next-parser itself; passing
  // it through the shell would brace-expand on bash and split it on zsh.
  const args = [
    "src/**/*.{ts,tsx}",
    "-c",
    "i18next-parser.config.cjs",
    "--output",
    `${tmpLocales}/$LOCALE/$NAMESPACE.json`,
  ];
  const result = spawnSync(cliBin, args, {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
    shell: false,
  });
  if (result.status !== 0) {
    console.error("[i18n:check] i18next-parser failed:");
    if (result.stderr) console.error(result.stderr.toString());
    if (result.stdout) console.error(result.stdout.toString());
    process.exit(1);
  }

  const drifts = [];
  const warnings = [];

  // --- 1. Each STATICALLY extracted (locale, ns) key must exist in the
  //        committed file. A key the parser sees but the JSON does not
  //        means a `t('new.key')` call site landed without its translation.
  //
  //        We do NOT fail on the inverse direction (committed-but-not-
  //        extracted) because the codebase intentionally constructs many
  //        keys at runtime — `t(\`page.status.${status}\`)`,
  //        `t(\`oauth.errors.${code}\`)`, etc. — which the static analyzer
  //        cannot resolve. Those are surfaced as warnings so a maintainer
  //        sees them locally but they do not block CI.
  for (const locale of LOCALES) {
    const namespaces = listJsonRelative(tmpLocales, locale);
    for (const ns of namespaces) {
      const extracted = flatten(
        readJson(path.join(tmpLocales, locale, `${ns}.json`)),
      );
      const committed = flatten(
        readJson(path.join(COMMITTED_LOCALES, locale, `${ns}.json`)),
      );
      const missing = setDiff(extracted, committed);
      const stale = setDiff(committed, extracted);
      if (missing.length) {
        drifts.push(
          `  - ${locale}/${ns}.json is MISSING ${missing.length} key(s):\n` +
            missing.map((k) => `      • ${k}`).join("\n"),
        );
      }
      if (stale.length) {
        // Listed, not just counted: a count alone is unactionable, and dead
        // keys (shipped roadmap copy, keys whose call site was deleted) hide
        // in this list next to the legitimately dynamic ones.
        warnings.push(
          `  - ${locale}/${ns}.json has ${stale.length} key(s) the static analyzer didn't see ` +
            `(constructed dynamically, or dead — verify each is still reachable):\n` +
            stale.map((k) => `      • ${k}`).join("\n"),
        );
      }
    }
  }

  // --- 2. EN ↔ KO key parity. Mirror per CLAUDE.md "EN/KO 번역 동시 반영".
  const enNs = new Set(listJsonRelative(COMMITTED_LOCALES, "en"));
  const koNs = new Set(listJsonRelative(COMMITTED_LOCALES, "ko"));
  for (const ns of new Set([...enNs, ...koNs])) {
    if (!enNs.has(ns)) {
      drifts.push(`  - en/${ns}.json is missing entirely (KO has it).`);
      continue;
    }
    if (!koNs.has(ns)) {
      drifts.push(`  - ko/${ns}.json is missing entirely (EN has it).`);
      continue;
    }
    const enKeys = flatten(
      readJson(path.join(COMMITTED_LOCALES, "en", `${ns}.json`)),
    );
    const koKeys = flatten(
      readJson(path.join(COMMITTED_LOCALES, "ko", `${ns}.json`)),
    );
    const missingInKo = setDiff(enKeys, koKeys);
    const missingInEn = setDiff(koKeys, enKeys);
    if (missingInKo.length) {
      drifts.push(
        `  - ko/${ns}.json is missing ${missingInKo.length} key(s) present in EN:\n` +
          missingInKo.map((k) => `      • ${k}`).join("\n"),
      );
    }
    if (missingInEn.length) {
      drifts.push(
        `  - en/${ns}.json is missing ${missingInEn.length} key(s) present in KO:\n` +
          missingInEn.map((k) => `      • ${k}`).join("\n"),
      );
    }

    // --- 3. Plural-suffix hygiene, per locale.
    drifts.push(...checkPluralSuffixes("en", ns, enKeys));
    drifts.push(...checkPluralSuffixes("ko", ns, koKeys));
  }

  // Cleanup. Best-effort — the OS tmpdir is cleared on reboot anyway.
  try {
    fs.rmSync(tmpRoot, { recursive: true, force: true });
  } catch {
    /* ignore */
  }

  if (drifts.length) {
    console.error(
      "[i18n:check] i18n drift detected — run `npm run i18n:extract` and commit, or add the missing KO/EN translations:\n",
    );
    console.error(drifts.join("\n\n"));
    console.error("");
    if (warnings.length) {
      console.error(
        "[i18n:check] non-fatal warnings (dynamic keys probably):\n",
      );
      console.error(warnings.join("\n"));
      console.error("");
    }
    process.exit(1);
  }

  // Warnings only → succeed but advise.
  if (warnings.length) {
    console.warn(
      "[i18n:check] non-fatal warnings (likely dynamic keys — verify reachability):",
    );
    console.warn(warnings.join("\n"));
  }
  console.log("[i18n:check] OK — locales are in sync.");
}

main();
