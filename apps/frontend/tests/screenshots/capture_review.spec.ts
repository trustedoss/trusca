/**
 * Design-review captures: the main screens at the widths the docs set skips.
 *
 * The guide screenshots are taken at 1440 x 900 in English light. Reviewers
 * also need to see how the layout holds at 1024 (the `lg` breakpoint, where
 * the sidebar stops being an overlay) and at 1280, in dark, and with the
 * longer Korean strings. This spec produces that matrix:
 *
 *   widths  1024, 1280 (height 800)
 *   modes   en-light, en-dark, ko-light
 *   screens 15 (see `SCREENS`)
 *
 * which is 90 PNGs named `<screen>--<width>-<lang>-<theme>.png` under
 * `tests/screenshots/.review/` (gitignored).
 *
 * It only runs through `playwright.review.config.ts`. The docs config
 * ignores this file, so `make screenshots-capture` and the
 * `capture_screenshots` dispatch produce exactly the assets they always
 * did. In CI it runs on the `capture_review` dispatch of ui-gates.yml, on
 * the same freshly seeded stack, and the folder is uploaded as the
 * `review-captures` artifact.
 *
 * Every test is independent (plain `describe`, not `serial`): one screen
 * failing to settle should not cost the reviewer the other 89 images.
 */
import { test, type Page } from "@playwright/test";

import { AdminAuditHarness } from "../_harness/AdminAuditHarness";
import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import {
  AUTHENTICATED_SCREENS,
  type ScreenContext,
} from "../_harness/representativeScreens";
import type { AuthenticatedScreenId } from "../_harness/screenIds";
import {
  expectThemeApplied,
  pinTheme,
  type GateTheme,
} from "../_harness/theme";
import {
  applyAuthFromSeed,
  captureReviewScreenshot,
  ensureSeededPendingApproval,
  expectUiLanguageApplied,
  readSeedProjectIds,
  setUiLanguage,
} from "./_helpers";

const WIDTHS = [1024, 1280] as const;
const HEIGHT = 800;

const MODES: ReadonlyArray<{ lang: "en" | "ko"; theme: GateTheme }> = [
  { lang: "en", theme: "light" },
  { lang: "en", theme: "dark" },
  { lang: "ko", theme: "light" },
];

type Visit = (page: Page, ctx: ScreenContext) => Promise<void>;

/**
 * Reuse the visual gate's navigation and settle logic where it already
 * covers a screen, so a review image and a baseline wait for the same thing.
 */
function registered(id: AuthenticatedScreenId): Visit {
  const screen = AUTHENTICATED_SCREENS.find((s) => s.id === id);
  if (!screen) throw new Error(`no representative screen "${id}"`);
  return screen.visit;
}

/** Authenticated screens, in the order a reviewer walks the product. */
const SCREENS: ReadonlyArray<{ name: string; visit: Visit }> = [
  { name: "dashboard", visit: registered("dashboard") },
  { name: "projects-list", visit: registered("projects-list") },
  { name: "project-overview", visit: registered("project-detail-overview") },
  { name: "components", visit: registered("project-detail-components") },
  {
    name: "vulnerabilities",
    visit: registered("project-detail-vulnerabilities"),
  },
  { name: "compliance", visit: registered("project-detail-compliance") },
  {
    name: "reports",
    visit: async (page, { projectId }) => {
      await page.goto(`/projects/${projectId}?tab=reports`);
      const portal = new PortalPage(page);
      await portal.expectProjectDetailMounted();
      await portal.expectReportsTabReady();
    },
  },
  { name: "scans", visit: registered("scans") },
  { name: "approvals", visit: registered("approvals") },
  { name: "policies", visit: registered("policies") },
  { name: "integrations", visit: registered("integrations") },
  { name: "notifications", visit: registered("notifications") },
  { name: "admin-users", visit: registered("admin-users") },
  {
    name: "admin-audit",
    visit: async (page) => {
      await page.goto("/admin/audit");
      await new AdminAuditHarness(page).expectMounted();
    },
  },
];

// One pending approval so the approvals screen shows a row rather than the
// empty state. Idempotent, and the docs capture relies on the same helper.
test.beforeAll(async () => {
  await ensureSeededPendingApproval();
});

for (const width of WIDTHS) {
  for (const { lang, theme } of MODES) {
    const suffix = `${width}-${lang}-${theme}`;

    test.describe(`@review ${suffix}`, () => {
      test.use({ viewport: { width, height: HEIGHT } });

      test(`login--${suffix}`, async ({ page }) => {
        const auth = new AuthHarness(page);
        await auth.clearAuthState();
        await pinTheme(page, theme);
        await setUiLanguage(page, lang);
        await auth.gotoLogin();
        await expectThemeApplied(page, theme);
        await expectUiLanguageApplied(page, lang);
        await captureReviewScreenshot(page, `login--${suffix}`);
      });

      test.describe("authenticated", () => {
        test.beforeEach(async ({ page }) => {
          await applyAuthFromSeed(page);
          await pinTheme(page, theme);
          await setUiLanguage(page, lang);
        });

        for (const screen of SCREENS) {
          test(`${screen.name}--${suffix}`, async ({ page }) => {
            await screen.visit(page, { projectId: readSeedProjectIds()[0] });
            await expectThemeApplied(page, theme);
            await expectUiLanguageApplied(page, lang);
            await captureReviewScreenshot(page, `${screen.name}--${suffix}`);
          });
        }
      });
    });
  }
}
