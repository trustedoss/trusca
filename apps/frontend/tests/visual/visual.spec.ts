/**
 * Marathon bundle 9 (4b) — visual regression baseline spec.
 *
 * Catches unintended UI drift on a curated set of canonical pages.
 * Baselines are stored under ``tests/visual/visual.spec.ts-snapshots/``
 * (Playwright's default ``snapshotPathTemplate``) and are diffed
 * pixel-by-pixel against fresh captures on every PR that the
 * ``ui-gates`` workflow runs.
 *
 * Which pages, and why these
 * ---------------------------
 * `tests/visual/coverage-manifest.ts` is the register: every screen the
 * router mounts is classified there as represented or exempt-with-reason,
 * and `tests/unit/design/visualCoverage.test.ts` fails if the two drift.
 * That contract is what keeps a screen added in a later wave from
 * defaulting to "unwatched" — the original 4-baseline set had no such
 * record, so growth simply went unnoticed.
 *
 * Every baseline is still a maintenance liability, so the set stays at
 * one-per-layout-template rather than one-per-route: auth, dashboard,
 * list, tabbed detail, virtualized table, status queue, workflow queue,
 * admin. A change to the shell chrome trips all of them at once, which
 * is the intent.
 *
 * What is excluded, and why
 * -------------------------
 * Relative timestamps are masked (`volatileRegions`) and the dev-server
 * devtools launcher is hidden (`hideDevChrome`). Both would fail the gate
 * for reasons unrelated to the UI — a ticking clock, a devtools version
 * bump — and a gate that cries wolf is one reviewers learn to skim past.
 *
 * Updating baselines (intentional UI change)
 * ------------------------------------------
 * Capture from CI, not locally — macOS font hinting diverges from the
 * linux runner by 5-20 % on text-heavy frames:
 *
 *   gh workflow run ui-gates.yml --ref <branch> \
 *      -f update_baselines=true
 *
 * then download the ``visual-baselines`` artifact and commit the PNGs.
 * On failure the workflow uploads the diff PNGs Playwright writes next
 * to the actual + expected images.
 */
import type { Page } from "@playwright/test";
import { test } from "@playwright/test";
import { expect } from "@playwright/test";
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

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Regions whose pixels legitimately change between runs.
 *
 * `<RelativeTime>` renders a semantic `<time dateTime>` element, so one
 * locator covers every relative stamp on the page — "3 minutes ago" ticking
 * over mid-suite is drift in the clock, not in the UI, and a baseline that
 * fails on it teaches reviewers to ignore this gate. Masking uniformly
 * (rather than per screen) means a screen that starts rendering a timestamp
 * later does not silently become flaky.
 */
function volatileRegions(page: Page) {
  return [
    page.locator("time"),
    // The dashboard's "last updated" stamp is plain text, not a <time>.
    page.getByTestId("dashboard-last-updated"),
  ];
}

/**
 * Hide dev-server-only chrome before capturing.
 *
 * The visual stack runs the Vite dev server, so `AppProviders` mounts the
 * TanStack Query devtools launcher in the bottom-right corner — a widget
 * production users never see. Left in frame it would pin every baseline to
 * the devtools package version: a routine dependency bump would redraw that
 * icon and turn all eight baselines red for a reason that has nothing to do
 * with the product. Hiding beats masking here — a mask would trade the icon
 * for an equally arbitrary magenta square.
 */
async function hideDevChrome(page: Page): Promise<void> {
  await page.addStyleTag({
    content: `
      .tsqd-parent-container,
      [aria-label="Open Tanstack query devtools"],
      [data-testid="tanstack-query-devtools"] { display: none !important; }
    `,
  });
}

function readPrimaryProjectId(): string {
  const seedPath = path.join(__dirname, "..", "screenshots", ".seed.json");
  const raw = JSON.parse(fs.readFileSync(seedPath, "utf8")) as {
    project_ids?: string[];
  };
  const id = raw.project_ids?.[0];
  if (!id) {
    throw new Error("seed missing project_ids[0]");
  }
  return id;
}

/**
 * Every screen, in both themes (W18).
 *
 * Sixteen baselines instead of eight. That is the cost of shipping a second
 * theme and there is no cheaper version of it: a dark palette changes every
 * pixel on every surface, so a light-only baseline set would report "no
 * visual change" for a dark regression of any size.
 *
 * Light keeps its bare filenames (`dashboard.png`) so the existing baselines
 * carry over untouched and this change adds files rather than rewriting them.
 */
test.describe.serial("@visual", () => {
  for (const theme of THEMES) {
    const suffix = themeSuffix(theme);

    test.describe(theme, () => {
      test(`login (pre-auth)${suffix}`, async ({ page }) => {
        const auth = new AuthHarness(page);
        await auth.clearAuthState();
        await pinTheme(page, theme);
        await auth.gotoLogin();
        await expectThemeApplied(page, theme);
        // Wait for any in-flight font swap before the snapshot — otherwise
        // the first capture on a cold runner uses Times New Roman fallback
        // and the diff trips at 100 %.
        await hideDevChrome(page);
        await page.evaluate(() => document.fonts.ready);
        await expect(page).toHaveScreenshot(`login${suffix}.png`);
      });

      test.describe("authenticated", () => {
        test.beforeEach(async ({ page }) => {
          await applyAuthFromSeed(page);
          await pinTheme(page, theme);
        });

        // Driven off the shared screen register so this spec and the a11y scan
        // can never disagree about which screens are covered.
        for (const screen of AUTHENTICATED_SCREENS) {
          test(`${screen.id}${suffix}`, async ({ page }) => {
            await screen.visit(page, { projectId: readPrimaryProjectId() });
            await expectThemeApplied(page, theme);
            await hideDevChrome(page);
            await page.evaluate(() => document.fonts.ready);
            await expect(page).toHaveScreenshot(`${screen.id}${suffix}.png`, {
              mask: volatileRegions(page),
            });
          });
        }
      });
    });
  }
});
