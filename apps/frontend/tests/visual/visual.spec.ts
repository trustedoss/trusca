/**
 * Marathon bundle 9 (4b) — visual regression baseline spec.
 *
 * Catches unintended UI drift on a curated set of canonical pages.
 * Baselines are stored under ``tests/visual/visual.spec.ts-snapshots/``
 * (Playwright's default ``snapshotPathTemplate``) and are diffed
 * pixel-by-pixel against fresh captures on every PR that the
 * ``visual-regression`` workflow runs.
 *
 * Pages covered (5 — kept small on purpose):
 *   - /login                — pre-auth surface, exercises form rendering
 *                             without any data dependency.
 *   - /projects             — list with seeded project rows.
 *   - /projects/<id> Overview — risk gauge + quick actions.
 *   - /projects/<id>?tab=vulnerabilities — virtualized table + drawer
 *                             closed.
 *   - /admin/dt             — admin DT status card.
 *
 * Why these five and not all 35? Visual regression has a flakiness
 * cost — each baseline image is a maintenance liability. Five is
 * enough to cover the four major layout templates (auth, list,
 * detail-tab, admin-card) plus one virtualized component. A drift
 * here is almost certainly a real regression.
 *
 * Updating baselines (intentional UI change):
 *   cd apps/frontend
 *   npx playwright test --config=playwright.visual.config.ts \
 *                       --update-snapshots
 *
 * The workflow uploads ``test-results/`` as an artifact on failure
 * so reviewers can inspect the diff PNG Playwright generates next
 * to the actual + expected images.
 */
import { test } from "@playwright/test";
import { expect } from "@playwright/test";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { applyAuthFromSeed } from "../screenshots/_helpers";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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

test.describe.serial("@visual", () => {
  test("login (pre-auth)", async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
    await auth.gotoLogin();
    // Wait for any in-flight font swap before the snapshot — otherwise
    // the first capture on a cold runner uses Times New Roman fallback
    // and the diff trips at 100 %.
    await page.evaluate(() => document.fonts.ready);
    await expect(page).toHaveScreenshot("login.png");
  });

  test.describe("authenticated", () => {
    test.beforeEach(async ({ page }) => {
      await applyAuthFromSeed(page);
    });

    test("projects list", async ({ page }) => {
      const portal = new PortalPage(page);
      await portal.gotoProjects();
      await portal.expectProjectListVisible();
      await page.evaluate(() => document.fonts.ready);
      await expect(page).toHaveScreenshot("projects-list.png");
    });

    test("project detail — overview", async ({ page }) => {
      await page.goto(`/projects/${readPrimaryProjectId()}`);
      const portal = new PortalPage(page);
      await portal.expectProjectDetailMounted();
      await page.evaluate(() => document.fonts.ready);
      await expect(page).toHaveScreenshot("project-detail-overview.png");
    });

    test("project detail — vulnerabilities tab", async ({ page }) => {
      await page.goto(`/projects/${readPrimaryProjectId()}?tab=vulnerabilities`);
      const portal = new PortalPage(page);
      await portal.expectProjectDetailMounted();
      await portal.expectVulnerabilitiesTabReady();
      await page.evaluate(() => document.fonts.ready);
      await expect(page).toHaveScreenshot("project-detail-vulnerabilities.png");
    });

    test("admin dt status", async ({ page }) => {
      const portal = new PortalPage(page);
      await portal.gotoAdminDT();
      await page
        .getByTestId("admin-dt-status-card")
        .waitFor({ state: "visible", timeout: 10_000 });
      await page.evaluate(() => document.fonts.ready);
      await expect(page).toHaveScreenshot("admin-dt-status.png");
    });
  });
});
