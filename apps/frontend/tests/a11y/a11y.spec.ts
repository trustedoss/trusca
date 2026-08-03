/**
 * G0-3 — accessibility gate (axe-core, WCAG 2.1 A/AA).
 *
 * Why a browser and not jsdom
 * ---------------------------
 * The rule this exists for is `color-contrast`, and axe cannot evaluate it
 * without a real rendering engine — the existing `badgeContrast.test.tsx`
 * says so in its own header and works around it by computing ratios by
 * hand for one component. That workaround does not scale to a dark theme
 * landing across every surface, which is what W15 does. So this runs
 * against the real stack, on the same representative screens the visual
 * baselines cover (`tests/_harness/representativeScreens.ts`).
 *
 * Why a ratchet and not zero
 * --------------------------
 * The app was never scanned before, so demanding zero violations would
 * mean either a very large unrelated fix or, more likely, the gate being
 * switched off. Existing violations are frozen per (screen, rule) in
 * `a11y-baseline.json` and the budget only travels downward — identical
 * in spirit to the design-token ratchet, and for the same reason: a gate
 * that cannot be satisfied is a gate that gets removed.
 *
 * Refreshing the baseline — on CI only (G0-9)
 * -------------------------------------------
 *   gh workflow run ui-gates.yml --ref <branch> -f update_baselines=true
 *
 * then download the `visual-baselines` artifact and commit the JSON it
 * contains. Run it after fixing violations (the numbers drop) or after
 * adding a screen. Never to make a new violation go away.
 *
 * A locally produced baseline is refused outright, because three times now
 * a local run has reported an improvement that did not exist: most recently
 * `projects-list color-contrast 4 -> 0`, which CI saw as 4 both times. The
 * local seed writes fewer rows, so the offending cells were never on
 * screen. Committing that would have handed back budget for violations that
 * are still there — the gate loosening itself, quietly, in the direction
 * nobody checks.
 *
 * `ALLOW_LOCAL_A11Y_BASELINE=1` exists for working on this spec itself, and
 * prints what it is doing. It is not a way to land a baseline.
 */
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { expect, test } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { AuthHarness } from "../_harness/auth";
import { AUTHENTICATED_SCREENS } from "../_harness/representativeScreens";
import {
  expectThemeApplied,
  pinTheme,
  THEMES,
  themeSuffix,
} from "../_harness/theme";
import { applyAuthFromSeed } from "../screenshots/_helpers";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BASELINE_PATH = path.join(__dirname, "a11y-baseline.json");
const OUTPUT_DIR = path.join(__dirname, ".output");
const OBSERVED_DIR = path.join(OUTPUT_DIR, "observed");
const UPDATE = process.env.A11Y_UPDATE_BASELINE === "1";

/**
 * Where a baseline is allowed to come from.
 *
 * The check is on the runner rather than on the person: a rule written in
 * an operations note is followed until the evening it is inconvenient, and
 * the resulting commit looks exactly like a legitimate one in review — a
 * smaller number, which is the shape of progress.
 */
const CI = process.env.CI === "true" || process.env.CI === "1";
const LOCAL_OVERRIDE = process.env.ALLOW_LOCAL_A11Y_BASELINE === "1";

if (UPDATE && !CI && !LOCAL_OVERRIDE) {
  throw new Error(
    "Refusing to write an accessibility baseline outside CI. The local seed " +
      "renders different data than the runner's, so a local count is not the " +
      "number this gate enforces — three times it has claimed an improvement " +
      "that was not real.\n\n" +
      "  gh workflow run ui-gates.yml --ref <branch> -f update_baselines=true\n\n" +
      "then commit the JSON from the `visual-baselines` artifact. To iterate " +
      "on this spec locally, set ALLOW_LOCAL_A11Y_BASELINE=1 — but do not " +
      "commit what it produces.",
  );
}

if (UPDATE && LOCAL_OVERRIDE && !CI) {
  console.warn(
    "\n  A11Y BASELINE: writing locally under ALLOW_LOCAL_A11Y_BASELINE.\n" +
      "  These counts reflect the local seed. Do not commit them.\n",
  );
}

/**
 * WCAG 2.1 A + AA. Deliberately not `best-practice`: those rules encode
 * Deque's house style rather than the standard the project committed to,
 * and mixing them in would make the baseline argue about taste.
 */
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

type Baseline = Record<string, Record<string, number>>;

function readBaseline(): Baseline {
  if (!fs.existsSync(BASELINE_PATH)) return {};
  return JSON.parse(fs.readFileSync(BASELINE_PATH, "utf8")) as Baseline;
}

export interface ScreenScan {
  /** Node count per rule id — this is what the ratchet compares. */
  counts: Record<string, number>;
  /**
   * CSS selector of each offending node, per rule. Counts alone tell you a
   * screen got worse; these tell you where, which is the difference between
   * a gate a contributor can act on and one they have to re-derive by hand.
   * Not part of the comparison — selectors churn with markup, and pinning
   * them would make every refactor look like a regression.
   */
  targets: Record<string, string[]>;
}

/**
 * Wait until every finite animation on the page has finished.
 *
 * The routed subtree fades in over `duration-slow` (AppShell.tsx). axe folds
 * an ancestor's opacity into the foreground colour it measures, so a scan
 * that lands inside those 250ms reads the muted grey as #7c7c84 rather than
 * #6c6c75 and reports a hundred contrast violations that do not exist once
 * the frame settles. Every screen's `visit` waits for its own content, but
 * content being mounted and the fade being over are different moments.
 *
 * It was a race, so it surfaced as a different set of screens each run —
 * which is the shape of a gate that eventually gets muted rather than
 * believed. Infinite animations (the skeleton pulse, spinners) are excluded
 * because waiting on those never returns; they are also the only animations
 * whose mid-flight frame IS the steady state, so their contrast is worth
 * measuring where it stands.
 */
async function settle(page: Page): Promise<void> {
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .filter((a) => a.effect?.getComputedTiming().iterations !== Infinity)
      .every((a) => a.playState === "finished" || a.playState === "idle"),
  );
}

/** Violations for one screen. */
async function scan(page: Page): Promise<ScreenScan> {
  await settle(page);

  const results = await new AxeBuilder({ page })
    .withTags(TAGS)
    // The devtools launcher is dev-server-only chrome that ships to nobody;
    // holding it to the product's accessibility bar would be noise.
    .exclude(".tsqd-parent-container")
    .analyze();

  const counts: Record<string, number> = {};
  const targets: Record<string, string[]> = {};
  for (const violation of results.violations) {
    counts[violation.id] = (counts[violation.id] ?? 0) + violation.nodes.length;
    targets[violation.id] = violation.nodes.map((node) =>
      node.target.join(" "),
    );
  }
  return { counts, targets };
}

/**
 * Compare one screen against its budget.
 *
 * Kept as plain data rather than a bare `expect` per rule so a failure
 * names every direction at once — what appeared, what grew, what shrank.
 */
export function compare(
  actual: Record<string, number>,
  budget: Record<string, number>,
) {
  const appeared: string[] = [];
  const grew: string[] = [];
  const shrank: string[] = [];

  for (const [rule, count] of Object.entries(actual)) {
    const allowed = budget[rule] ?? 0;
    if (allowed === 0) appeared.push(`${rule} (${count})`);
    else if (count > allowed) grew.push(`${rule} ${allowed} → ${count}`);
    else if (count < allowed) shrank.push(`${rule} ${allowed} → ${count}`);
  }
  for (const [rule, allowed] of Object.entries(budget)) {
    if (!(rule in actual)) shrank.push(`${rule} ${allowed} → 0`);
  }

  return { appeared, grew, shrank };
}

// Not `.serial`: in serial mode the first failure aborts the rest, so one
// bad screen would hide the state of the other six. Sequencing already
// comes from `workers: 1` in the config.
test.describe("@a11y", () => {
  const baseline = readBaseline();

  function assertScreen(id: string, result: ScreenScan) {
    const actual = result.counts;
    // Written to disk immediately, not accumulated in memory: Playwright
    // restarts the worker process after a failed test, which would reset
    // any module-level tally and leave the summary showing only whatever
    // the last worker happened to see. Playwright clears `outputDir` at the
    // start of a run, so yesterday's files cannot leak in.
    fs.mkdirSync(OBSERVED_DIR, { recursive: true });
    fs.writeFileSync(
      path.join(OBSERVED_DIR, `${id}.json`),
      JSON.stringify(result, null, 2),
    );
    if (UPDATE) return;

    const { appeared, grew, shrank } = compare(actual, baseline[id] ?? {});

    // One assertion, three directions — `shrank` fails on purpose: a fix
    // that is not recorded leaves budget behind for the next regression to
    // spend silently.
    expect(
      { screen: id, appeared, grew, shrank },
      appeared.length + grew.length > 0
        ? "New accessibility violations. Fix them, or if this is a deliberate " +
            "exception, say so in the PR and record it."
        : "Violations dropped — commit the lowered baseline: " +
            "A11Y_UPDATE_BASELINE=1 npx playwright test --config=playwright.a11y.config.ts",
    ).toEqual({ screen: id, appeared: [], grew: [], shrank: [] });
  }

  /**
   * Every screen, in both themes (W18).
   *
   * This is the half of the theme work a person cannot do by looking. Dark
   * inverts which contrasts are tight: the severity hexes that read on white
   * fall under AA on a dark card, and the ones that were marginal in light
   * become comfortable. Scanning only light would leave a whole theme's
   * contrast unmeasured — and W18 found four separate defects of exactly that
   * shape before this pass existed (`text-destructive`, 58 severity-as-text
   * call sites, the pale scrim, the stale graph palette).
   *
   * Light keeps its bare screen ids so the existing budget carries over; dark
   * gets its own keys and its own numbers, because they are not the same
   * numbers.
   */
  for (const theme of THEMES) {
    const suffix = themeSuffix(theme);

    test.describe(theme, () => {
      test(`login (pre-auth)${suffix}`, async ({ page }) => {
        const auth = new AuthHarness(page);
        await auth.clearAuthState();
        await pinTheme(page, theme);
        await auth.gotoLogin();
        await expectThemeApplied(page, theme);
        assertScreen(`login${suffix}`, await scan(page));
      });

      test.describe("authenticated", () => {
        test.beforeEach(async ({ page }) => {
          await applyAuthFromSeed(page);
          await pinTheme(page, theme);
        });

        for (const screen of AUTHENTICATED_SCREENS) {
          test(`${screen.id}${suffix}`, async ({ page }) => {
            await screen.visit(page, { projectId: readPrimaryProjectId() });
            await expectThemeApplied(page, theme);
            assertScreen(`${screen.id}${suffix}`, await scan(page));
          });
        }
      });
    });
  }

  test.afterAll(() => {
    if (!fs.existsSync(OBSERVED_DIR)) return;
    const merged: Baseline = {};
    for (const file of fs.readdirSync(OBSERVED_DIR)) {
      if (!file.endsWith(".json")) continue;
      const observed = JSON.parse(
        fs.readFileSync(path.join(OBSERVED_DIR, file), "utf8"),
      ) as ScreenScan;
      merged[file.replace(/\.json$/, "")] = observed.counts;
    }

    const sorted = Object.fromEntries(
      Object.entries(merged)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([id, rules]) => [
          id,
          Object.fromEntries(
            Object.entries(rules).sort(([a], [b]) => a.localeCompare(b)),
          ),
        ]),
    );
    const serialised = `${JSON.stringify(sorted, null, 2)}\n`;

    // Always leave the observed state behind, pass or fail. On a failure
    // it tells the reviewer what the numbers actually are instead of
    // making them reconstruct it from log lines, and it is how the very
    // first baseline gets bootstrapped — before one exists there is
    // nothing to dispatch an update against.
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
    fs.writeFileSync(path.join(OUTPUT_DIR, "a11y-observed.json"), serialised);

    if (UPDATE) fs.writeFileSync(BASELINE_PATH, serialised);
  });
});

function readPrimaryProjectId(): string {
  const seedPath = path.join(__dirname, "..", "screenshots", ".seed.json");
  const raw = JSON.parse(fs.readFileSync(seedPath, "utf8")) as {
    project_ids?: string[];
  };
  const id = raw.project_ids?.[0];
  if (!id) throw new Error("seed missing project_ids[0]");
  return id;
}
