// run-modules — vendored verify specs, nightly entrypoint.
//
// Runs every spec module under specs/ through the (vendored, unmodified)
// verify-runner, applying excluded.json, and exits non-zero if any
// non-excluded check fails. The runner itself is the verification team's
// artifact (see PROVENANCE.md); this wrapper is ours and is the ONLY place
// exclusion logic lives — specs and runner stay byte-faithful to the
// snapshot.
//
// No-silent-caps: every exclusion comes from excluded.json with a written
// reason, and the summary prints how many checks were excluded per module so
// a shrinking allowlist is visible in the nightly log, never implicit.
//
// Usage:
//   node tests/verify-specs/run-modules.mjs            # all modules
//   node tests/verify-specs/run-modules.mjs scans sbom # subset

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runSpec } from "./verify-runner.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const SPEC_DIR = join(HERE, "specs");
const EXCLUDED = JSON.parse(readFileSync(join(HERE, "excluded.json"), "utf8"));

const wanted = process.argv.slice(2);
const modules = readdirSync(SPEC_DIR)
  .filter((f) => f.endsWith(".json"))
  .map((f) => f.replace(/\.json$/, ""))
  .filter((m) => wanted.length === 0 || wanted.includes(m))
  .sort();

const excludedModules = new Set(Object.keys(EXCLUDED.modules ?? {}));
const excludedChecks = EXCLUDED.checks ?? {};

let totalPass = 0;
let totalFail = 0;
let totalExcluded = 0;
const failures = [];

for (const mod of modules) {
  if (excludedModules.has(mod)) {
    const reason = EXCLUDED.modules[mod];
    console.log(`[skip-module] ${mod} — ${reason}`);
    totalExcluded += 1;
    continue;
  }
  let results;
  try {
    ({ results } = await runSpec(mod));
  } catch (err) {
    console.log(`[module-error] ${mod} — ${err.message}`);
    failures.push({ module: mod, tc: "(module)", detail: err.message });
    totalFail += 1;
    continue;
  }
  let pass = 0;
  let fail = 0;
  let excl = 0;
  for (const r of results) {
    const key = `${mod}:${r.tc}`;
    if (key in excludedChecks) {
      excl += 1;
      continue;
    }
    if (r.verdict === "pass") {
      pass += 1;
    } else {
      fail += 1;
      failures.push({ module: mod, tc: r.tc, detail: r.detail ?? r.verdict });
    }
  }
  totalPass += pass;
  totalFail += fail;
  totalExcluded += excl;
  const exclNote = excl ? ` excluded=${excl}` : "";
  console.log(`[${mod}] pass=${pass} fail=${fail}${exclNote}`);
}

console.log(
  `\n[summary] modules=${modules.length} pass=${totalPass} ` +
    `fail=${totalFail} excluded=${totalExcluded}`,
);
if (failures.length) {
  console.log("\n[failures]");
  for (const f of failures) {
    console.log(`  ${f.module} ${f.tc}: ${String(f.detail).slice(0, 200)}`);
  }
  process.exit(1);
}
