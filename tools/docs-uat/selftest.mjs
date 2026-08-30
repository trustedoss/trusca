#!/usr/bin/env node
/**
 * docs-uat selftest: asserts the extractor's own logic, in particular the
 * waiver census added for E3 (testing-hardening-plan-2026-08.md, section 2).
 * Pure functions only, so this needs no dev stack and no repository checkout
 * in any particular condition. Run it with `node tools/docs-uat/selftest.mjs`.
 *
 * Same shape as tools/em-dash/selftest.mjs and tools/license-header/selftest.mjs.
 */
import { strict as assert } from "node:assert";

import { buildManifest, summarize, waiverCensus } from "./extract.mjs";

let passed = 0;

function check(name, fn) {
  try {
    fn();
    passed += 1;
    console.log(`ok   ${name}`);
  } catch (err) {
    console.error(`FAIL ${name}\n     ${err.message}`);
    process.exitCode = 1;
  }
}

/** Build a minimal fake manifest; waiverCensus only reads `.steps`. */
function fakeManifest(steps) {
  return { docs: [], steps };
}

// --- waiverCensus on synthetic input -----------------------------------------

check("counts zero waivers on an all-executed manifest", () => {
  const m = fakeManifest([
    { id: "a", waiver: undefined },
    { id: "b", waiver: undefined },
  ]);
  assert.deepEqual(waiverCensus(m), { total: 2, waived: 0, byReason: {} });
});

check("counts a single waiver reason", () => {
  const m = fakeManifest([
    { id: "a", waiver: "needs-live-cluster" },
    { id: "b", waiver: undefined },
  ]);
  assert.deepEqual(waiverCensus(m), {
    total: 2,
    waived: 1,
    byReason: { "needs-live-cluster": 1 },
  });
});

check("groups repeated reasons and leaves unseen reasons out entirely", () => {
  const m = fakeManifest([
    { id: "a", waiver: "env-config-snippet-not-a-command" },
    { id: "b", waiver: "env-config-snippet-not-a-command" },
    { id: "c", waiver: "needs-live-cluster" },
    { id: "d", waiver: undefined },
  ]);
  const census = waiverCensus(m);
  assert.equal(census.total, 4);
  assert.equal(census.waived, 3);
  assert.deepEqual(census.byReason, {
    "env-config-snippet-not-a-command": 2,
    "needs-live-cluster": 1,
  });
  // Every waived step lands in exactly one reason bucket, so the sum of the
  // per-reason counts always reconciles with the waived total.
  const reasonSum = Object.values(census.byReason).reduce((a, b) => a + b, 0);
  assert.equal(reasonSum, census.waived);
});

check("treats an empty-string waiver as unwaived (falsy, no reason to tally)", () => {
  const m = fakeManifest([{ id: "a", waiver: "" }]);
  assert.deepEqual(waiverCensus(m), { total: 1, waived: 0, byReason: {} });
});

check("does not invent reasons that never appear in the input", () => {
  const m = fakeManifest([{ id: "a", waiver: "brand-new-reason-nobody-declared" }]);
  assert.deepEqual(waiverCensus(m).byReason, { "brand-new-reason-nobody-declared": 1 });
});

// --- summarize() carries the census alongside the existing tier/kind tally --

check("summarize() embeds waiverCensus without disturbing byTier/byKind", () => {
  const m = fakeManifest([
    { id: "a", tier: "gate", kind: "shell", waiver: undefined },
    { id: "b", tier: "gate", kind: "shell", waiver: "needs-live-cluster" },
  ]);
  const sum = summarize(m);
  assert.deepEqual(sum.byTier, { gate: 2 });
  assert.deepEqual(sum.byKind, { shell: 2 });
  assert.deepEqual(sum.waivers, {
    total: 2,
    waived: 1,
    byReason: { "needs-live-cluster": 1 },
  });
});

// --- integration: the real docs tree reconciles internally -------------------
// Not a fixed-count assertion (the plan doc's "~87 of ~250" is a snapshot the
// count is expected to move as doc-writer clears waivers), but the invariants
// that would break if the census logic double-counted or dropped a step must
// hold no matter how many docs are currently enrolled.

check("real manifest: waived count never exceeds total steps", () => {
  const manifest = buildManifest();
  const census = waiverCensus(manifest);
  assert.ok(census.total >= census.waived, "waived cannot exceed total");
  assert.equal(census.total, manifest.steps.length);
});

check("real manifest: byReason sums back to the waived total", () => {
  const manifest = buildManifest();
  const census = waiverCensus(manifest);
  const reasonSum = Object.values(census.byReason).reduce((a, b) => a + b, 0);
  assert.equal(reasonSum, census.waived);
});

check("real manifest: every waived step actually carries waiver= (no false positives)", () => {
  const manifest = buildManifest();
  const waivedIds = new Set(manifest.steps.filter((s) => s.waiver).map((s) => s.id));
  const census = waiverCensus(manifest);
  assert.equal(waivedIds.size, census.waived);
});

console.log(
  process.exitCode ? `\ndocs-uat selftest: FAILED` : `\ndocs-uat selftest: OK (${passed} check(s))`,
);
