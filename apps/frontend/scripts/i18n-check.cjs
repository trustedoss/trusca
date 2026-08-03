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
 *   4. Exit 0 on green, 1 on any drift with an actionable message.
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
        warnings.push(
          `  - ${locale}/${ns}.json has ${stale.length} key(s) the static analyzer didn't see ` +
            `(probably constructed dynamically — verify each is still reachable).`,
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
