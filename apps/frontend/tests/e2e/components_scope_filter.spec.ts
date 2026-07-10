/**
 * Components scope-filter note E2E — Phase K (PR K-2).
 *
 * The runtime-scope filter (backend PR K-1) drops dev/test dependencies from
 * source-scan SBOMs and records `{ applied, dropped, kept }` on
 * `Scan.scan_metadata.scope_filter`. This spec pins the transparency surface:
 * the Components tab's summary band shows an "excluded N" note sourced from
 * that telemetry.
 *
 *   S1 — A project whose seeded scan carries scope_filter telemetry shows
 *        the note with `data-dropped` = the summed per-ecosystem counts
 *        (seed writes maven=3 + npm=12 → 15), and the per-ecosystem
 *        breakdown on the title attribute.
 *   S2 — The note is additive INSIDE the existing summary band (the
 *        loaded/total copy keeps rendering) — it never replaces the band.
 *        A scan without telemetry renders no note (pinned by the unit
 *        tests in tests/unit/parseScopeFilter.test.ts — absence needs no
 *        second seed here).
 *
 * Selectors anchor on `data-testid="components-scope-filter-note"` and
 * `data-dropped` — never the translated copy — so the suite passes on EN
 * and KO alike.
 *
 * Auth + seed run ONCE for the file (serial describe + shared page): login
 * is rate-limited at 5/IP/min, so one login serves every scenario.
 *
 * Pre-requisites (auto-skip otherwise): docker-compose dev stack up,
 * python3 on PATH (seed harness validates).
 */
import { type Page, expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const FILTERED_PROJECT = "ci-scope-filter";
const EXPECTED_DROPPED = 15; // seed writes {maven: 3, npm: 12}

let sharedPage: Page;
let seedFailed = false;
let seed: SeedSummary;

test.describe.serial("@components scope-filter summary note", () => {
  test.beforeAll(async ({ browser }) => {
    sharedPage = await browser.newPage();
    try {
      seed = seedE2eUser({
        projectNames: [FILTERED_PROJECT],
        withScan: true,
        componentCount: 5,
      });
    } catch {
      seedFailed = true;
      return;
    }
    const auth = new AuthHarness(sharedPage);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);
  });

  test.afterAll(async () => {
    await sharedPage?.close();
  });

  test("S1) summary band shows the excluded-components note with the summed count", async () => {
    test.skip(seedFailed, "seed failed — dev stack not reachable");
    const portal = new PortalPage(sharedPage);
    await portal.gotoProjects();
    await portal.openProjectDetail(FILTERED_PROJECT);
    await portal.selectTab("components");

    const note = sharedPage.getByTestId("components-scope-filter-note");
    await expect(note).toBeVisible();
    await expect(note).toHaveAttribute("data-dropped", String(EXPECTED_DROPPED));
    // The per-ecosystem breakdown rides the title attribute for operators.
    await expect(note).toHaveAttribute("title", /maven: 3/);
    await expect(note).toHaveAttribute("title", /npm: 12/);
  });

  test("S2) the summary band itself renders regardless (note is additive)", async () => {
    test.skip(seedFailed, "seed failed — dev stack not reachable");
    // The note must live INSIDE the existing summary band, not replace it —
    // the loaded/total copy stays the band's first child.
    const band = sharedPage.getByTestId("components-summary");
    await expect(band).toBeVisible();
    await expect(
      band.getByTestId("components-scope-filter-note"),
    ).toBeVisible();
  });
});
