/**
 * G0-2 — router ↔ visual-coverage manifest contract.
 *
 * The screen vocabulary lives in two places: `router.tsx` decides what can
 * render, and `coverage-manifest.ts` records whether a baseline guards it.
 * CLAUDE.md's hardening rule #2 applies — when the same vocabulary lives in
 * two places, a consistency test is mandatory, because each side stays
 * internally green while they drift apart.
 *
 * Concretely, this is what stops a screen added in a future wave from
 * quietly defaulting to "nobody is watching it": the route mounts a new
 * component, the manifest has no entry, this test fails and asks the author
 * to either add a baseline or write down why not.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ALL_SCREEN_IDS } from "../../_harness/screenIds";
import {
  REPRESENTED_SNAPSHOTS,
  VISUAL_COVERAGE,
} from "../../visual/coverage-manifest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");
const ROUTER_PATH = path.join(FRONTEND_ROOT, "src", "router.tsx");
const SNAPSHOT_DIR = path.join(
  FRONTEND_ROOT,
  "tests",
  "visual",
  "visual.spec.ts-snapshots",
);

/**
 * Components that carry no screen of their own: layout wrappers, auth
 * guards, and the redirect element. They mount routes rather than being
 * one, so classifying them would be noise.
 */
const NOT_A_SCREEN = new Set([
  "AppShell",
  "RequireAuth",
  "AdminLayout",
  "Navigate",
]);

/** Component names mounted as `element={<Xxx ... />}` in the route table. */
function routedComponents(): string[] {
  const source = fs.readFileSync(ROUTER_PATH, "utf8");
  const names = new Set<string>();
  for (const match of source.matchAll(/element=\{<([A-Z][A-Za-z0-9]*)/g)) {
    if (!NOT_A_SCREEN.has(match[1])) names.add(match[1]);
  }
  return [...names].sort();
}

describe("visual coverage manifest", () => {
  it("classifies every screen the router can mount", () => {
    const routed = routedComponents();
    const classified = Object.keys(VISUAL_COVERAGE).sort();

    const unclassified = routed.filter((name) => !classified.includes(name));
    const stale = classified.filter((name) => !routed.includes(name));

    // Reported as one assertion so a failure names both directions at once.
    expect({ unclassified, stale }).toEqual({ unclassified: [], stale: [] });
  });

  it("found a non-trivial number of routes (the parser still works)", () => {
    // Guards against a router refactor that changes the `element={<X />}`
    // shape: the regex would quietly match nothing, both lists would be
    // empty, and the contract above would pass while asserting nothing.
    expect(routedComponents().length).toBeGreaterThan(15);
  });

  it("declares a baseline file for every represented screen", () => {
    const missing = REPRESENTED_SNAPSHOTS.filter(
      (name) => !fs.existsSync(path.join(SNAPSHOT_DIR, name)),
    );

    expect(missing).toEqual([]);
  });

  it("keeps the snapshot directory free of orphan baselines", () => {
    // A baseline left behind by a deleted page is dead weight that still
    // reads as coverage. Platform-suffixed PNGs are excluded: an operator
    // running --update-snapshots on macOS/Windows writes files the spec
    // never reads (snapshotPathTemplate strips the suffix), and
    // tests/visual/.gitignore already keeps them out of the repo.
    const orphans = fs
      .readdirSync(SNAPSHOT_DIR)
      .filter((name) => name.endsWith(".png"))
      .filter((name) => !/-chromium-(darwin|win32)\.png$/.test(name))
      .filter((name) => !REPRESENTED_SNAPSHOTS.includes(name));

    expect(orphans).toEqual([]);
  });

  it("matches the screen ids the gates actually walk, in both themes", () => {
    // Both gates derive their screen list from `screenIds.ts`, and the
    // baseline file stem is the id. Asserting against that register rather
    // than grepping the spec source keeps the contract independent of how
    // the spec is written — an earlier version of this test scanned for
    // string literals and broke the moment the spec started building the
    // name from a template.
    //
    // W18 doubled the set: every screen is captured light and dark. Deriving
    // the pair here from the same register means a screen cannot end up with
    // one theme baselined and the other silently missing, which would read
    // as full coverage on a dashboard of green ticks.
    const expected = ALL_SCREEN_IDS.flatMap((id) => [
      `${id}.png`,
      `${id}-dark.png`,
    ]).sort();

    expect([...REPRESENTED_SNAPSHOTS].sort()).toEqual(expected);
  });
});
