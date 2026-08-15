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
 *
 * Database identifiers belong here for the same reason and no other. The
 * seed's generated names are pinned in CI instead of masked, because they
 * are text the product lays out and the gate should see; a primary key is
 * assigned by Postgres and cannot be pinned without seeding rows by hand.
 */
function volatileRegions(page: Page) {
  return [
    page.locator("time"),
    // The dashboard's "last updated" stamp is plain text, not a <time>.
    page.getByTestId("dashboard-last-updated"),
    // The project's uuid, rendered under the heading on every project tab.
    page.getByTestId("project-detail-id"),
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

/**
 * Block until the web fonts the product actually renders with are loaded.
 *
 * `document.fonts.ready` is not enough on its own. It resolves when no font
 * load is in flight, and on a cold page nothing is in flight yet: the
 * `@font-face` rules arrive with a stylesheet fetched from a font CDN, and
 * until that stylesheet lands the browser has no font to load and reports
 * itself ready. A capture taken then renders in the fallback face.
 *
 * Two back-to-back baseline captures from one commit caught exactly that on
 * 2026-08-14: `projects-list.png` came out in the fallback face in one of
 * them, at 1.8 % of the viewport. The ceiling at the time was 2 %, so a
 * screenshot in the wrong typeface passed the gate.
 *
 * So the wait is in two parts. At least one Inter face has to have reached
 * `loaded`, which cannot happen before the stylesheet registers the faces,
 * and no Inter face may still be in flight. Naming a weight instead would be
 * wrong: a browser fetches only the faces a page uses, so asking for
 * `600 16px Inter` on a screen with no semibold text waits forever, which is
 * how the first version of this failed on admin-users.
 *
 * The second clause used to read `document.fonts.status`, which is a
 * document-wide snapshot: it returns to "loading" for ANY family, so a
 * single unrelated face that never settles held up a capture whose own font
 * had been ready for seconds. That timed out four times on 2026-08-15,
 * across two screens, with nothing wrong in the screenshots. Scoping the
 * clause to Inter keeps both guarantees and drops the dependency on
 * everything else the page happens to fetch.
 *
 * If the stylesheet never lands the wait times out and the test fails, which
 * is the honest outcome: the alternative is a baseline recording the wrong
 * font. On that timeout it now reports which faces were in what state,
 * because four investigations of a bare "Timeout 20000ms exceeded" produced
 * four guesses and no cause.
 */
async function waitForWebFonts(page: Page): Promise<void> {
  try {
    await page.waitForFunction(
      () => {
        const faces = Array.from(document.fonts);
        const inter = faces.filter((f) => f.family === "Inter");
        return (
          inter.length > 0 &&
          inter.some((f) => f.status === "loaded") &&
          // Scoped to Inter, not `document.fonts.status`. That property is
          // a snapshot of the whole document and returns to "loading" for
          // any family, so one unrelated face that never settles blocked
          // a capture whose own font had been ready for seconds. This
          // timed out four times on 2026-08-15, on two different screens,
          // while the screenshots themselves were fine.
          //
          // The guarantee that matters is unchanged: at least one Inter
          // face loaded, and no Inter face still in flight.
          inter.every((f) => f.status !== "loading")
        );
      },
      undefined,
      { timeout: 20_000 },
    );
  } catch (err) {
    // Say which face was stuck. The previous version failed with a bare
    // "Timeout 20000ms exceeded" pointing at the line, which told four
    // separate investigations nothing and left the cause a guess.
    const state = await page.evaluate(() =>
      Array.from(document.fonts).map((f) => ({
        family: f.family,
        weight: f.weight,
        style: f.style,
        status: f.status,
      })),
    );
    throw new Error(
      `waitForWebFonts timed out. document.fonts.status=` +
        `${await page.evaluate(() => document.fonts.status)}, faces=` +
        `${JSON.stringify(state)}\n\nOriginal: ${
          err instanceof Error ? err.message : String(err)
        }`,
    );
  }
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
        await hideDevChrome(page);
        await waitForWebFonts(page);
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
            await waitForWebFonts(page);
            await expect(page).toHaveScreenshot(`${screen.id}${suffix}.png`, {
              mask: volatileRegions(page),
            });
          });
        }
      });
    });
  }
});
