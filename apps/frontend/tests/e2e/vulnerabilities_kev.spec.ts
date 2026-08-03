/**
 * Vulnerabilities KEV E2E — Phase C / C4 (CISA Known Exploited Vulnerabilities).
 *
 * Sibling of `vulnerabilities_epss.spec.ts` (same sharedPage + seed-skip
 * conventions). Exercises the KEV surfaces of the project-detail
 * Vulnerabilities tab against the docker-compose dev stack:
 *
 *   K1 — Catalog-listed CVEs carry the KEV badge; non-listed rows carry
 *        none (absence is the signal — the badge renders ONLY for
 *        `kev=true`, per the KevBadge contract).
 *   K2 — The DEFAULT sort is the composite `priority` ranking (KEV →
 *        severity → EPSS): the select reads `priority`, the URL carries no
 *        `?sort=`, and every KEV row precedes every non-KEV row. Switching
 *        the sort select to `severity` mirrors `?sort=severity` in the URL.
 *   K3 — The seeded SLA spread surfaces all three `data-due-state` values
 *        (overdue / imminent / ok) — the C3 deadline visualization.
 *   K4 — The drawer's KEV badge shows the remediation due date inline
 *        (`kev-badge-due-date`), with the raw ISO date on
 *        `data-kev-due-date`.
 *
 * All selectors live in `apps/frontend/tests/_harness/PortalPage.ts`. Every
 * assertion anchors on `data-testid` / `data-kev` / `data-due-state` /
 * `data-kev-due-date` — never the translated "KEV" label or localized due
 * copy — so the suite passes identically on EN and KO.
 *
 * Auth + seed run ONCE for the whole file (a `serial` describe sharing one
 * page): the auth backend rate-limits login at 5/IP/min, so re-logging-in
 * per test would risk a 429 on a single-IP run. One login + one seed also
 * keeps every scenario reading the same deterministic fixture.
 *
 * Pre-requisites (auto-skip otherwise — see `beforeAll`):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed harness validates this)
 *
 * Seed: vulnerability-mode (`--vulnerability-count 12 --kev-count 6`). The
 * first 6 CVEs of the seed plan get `kev=true` with due dates cycled
 * through the default `overdue,imminent,ok` spread (today − 3 / + 3 /
 * + 30 days) → 2 rows per SLA state; the remaining 6 stay `kev=false`.
 * The offsets sit inside the FE `dueDate.ts` bands with margin, so a
 * UTC↔local calendar-day skew can never flip a seeded state.
 */
import { type Page, expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-kev";
const VULNERABILITY_COUNT = 12;
const KEV_COUNT = 6;
const DUE_STATES = ["overdue", "imminent", "ok"] as const;

// Shared across the serial block — created once in beforeAll.
let sharedPage: Page;
let seedFailed = false;

test.describe.serial("@critical @vulnerabilities project vulnerabilities KEV", () => {
  test.beforeAll(async ({ browser }) => {
    sharedPage = await browser.newPage();

    let seed: SeedSummary;
    try {
      seed = seedE2eUser({
        projectNames: [PROJECT_NAME],
        withScan: true,
        // Vulnerability-mode PURLs are run-suffixed by the script, so no
        // cross-run collision handling is needed (unlike component-mode —
        // see vulnerabilities_epss.spec.ts).
        vulnerabilityCount: VULNERABILITY_COUNT,
        kevCount: KEV_COUNT,
        // Default spread ("overdue,imminent,ok") — stated explicitly so the
        // K3 three-state assertion is self-documenting at the call site.
        kevDueSpread: "overdue,imminent,ok",
      });
    } catch {
      // Stack not up / python missing — flag so every test self-skips with a
      // clear reason (we can't call test.skip from beforeAll directly).
      seedFailed = true;
      return;
    }
    expect(seed.kev_count).toBe(KEV_COUNT);

    const auth = new AuthHarness(sharedPage);
    await auth.clearAuthState();
    await auth.gotoLogin();
    await auth.login(seed.email, seed.password);

    const portal = new PortalPage(sharedPage);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();
  });

  test.afterAll(async () => {
    await sharedPage?.close();
  });

  test.beforeEach(async () => {
    test.skip(
      seedFailed,
      "seed precondition failed — bring docker-compose dev up + ensure python3 is on PATH",
    );
    // Re-enter the tab from the project list each time so every scenario
    // starts from the unfiltered default-sort list (a clean
    // `?tab=vulnerabilities` URL), independent of whatever sort/drawer the
    // previous test left behind on the shared page.
    const portal = new PortalPage(sharedPage);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectVulnerabilitiesTab();
  });

  test("K1) KEV badge renders on catalog-listed CVEs and only on them", async () => {
    const portal = new PortalPage(sharedPage);

    const states = await portal.getMountedRowKevStates();
    expect(states.length).toBeGreaterThanOrEqual(2);

    // The fixture guarantees both populations are on screen.
    const kevRows = states.filter((s) => s.kev);
    const nonKevRows = states.filter((s) => !s.kev);
    expect(kevRows.length).toBeGreaterThanOrEqual(1);
    expect(nonKevRows.length).toBeGreaterThanOrEqual(1);

    // Row-level contract: badge present ⇔ `data-kev="true"`. Assert one
    // concrete row of each population through the scoped verbs so the
    // badge (not just the data flag) is what's checked.
    expect(kevRows[0].cveId).toBeTruthy();
    await portal.expectRowKevBadge(kevRows[0].cveId as string);
    expect(nonKevRows[0].cveId).toBeTruthy();
    await portal.expectRowWithoutKevBadge(nonKevRows[0].cveId as string);

    // And every mounted KEV row carries a due state (the seed always sets a
    // due date), while badge-less rows report none.
    for (const row of kevRows) {
      expect(DUE_STATES).toContain(row.dueState);
    }
    for (const row of nonKevRows) {
      expect(row.dueState).toBeNull();
    }
  });

  test("K2) default sort is priority (KEV first); selecting severity mirrors the URL", async () => {
    const portal = new PortalPage(sharedPage);

    // (a) Default surface state: the select reads the composite `priority`
    // key and the URL carries NO `?sort=` (the default is never mirrored).
    expect(await portal.getVulnerabilitiesSortKey()).toBe("priority");
    expect(new URL(sharedPage.url()).searchParams.get("sort")).toBeNull();

    // (b) Priority ranking puts KEV rows on top: once a non-KEV row
    // appears, no KEV row may follow it (same shape as the EPSS
    // NULLS-LAST assertion).
    const states = await portal.getMountedRowKevStates();
    const firstNonKevIdx = states.findIndex((s) => !s.kev);
    expect(states[0]?.kev).toBe(true);
    if (firstNonKevIdx !== -1) {
      for (let i = firstNonKevIdx; i < states.length; i++) {
        expect(states[i].kev).toBe(false);
      }
    }

    // (c) Switching to a column key via the select mirrors the URL.
    await portal.sortVulnerabilitiesBy("severity");
    expect(new URL(sharedPage.url()).searchParams.get("sort")).toBe(
      "severity",
    );
    expect(await portal.getVulnerabilitiesSortKey()).toBe("severity");
  });

  test("K3) SLA spread surfaces all three due states (overdue / imminent / ok)", async () => {
    const portal = new PortalPage(sharedPage);

    const states = await portal.getMountedRowKevStates();
    const seen = states
      .map((s) => s.dueState)
      .filter((s): s is string => s != null);

    // 6 KEV rows cycled through the 3-token spread → 2 badges per state.
    for (const required of DUE_STATES) {
      expect(seen).toContain(required);
    }
  });

  test("K4) drawer shows the remediation due date inline", async () => {
    const portal = new PortalPage(sharedPage);

    // Under the default priority sort the top row is a KEV row (pinned by
    // K2); open its drawer by CVE id so the click is unambiguous.
    const states = await portal.getMountedRowKevStates();
    const kevRow = states.find((s) => s.kev && s.cveId != null);
    expect(kevRow).toBeTruthy();
    await portal.openVulnerabilityDrawer(kevRow?.cveId as string);

    // The drawer's meta strip renders the badge with the inline due text
    // (`showDueDate` surface). The verb scopes to the drawer so the row
    // badge behind the sheet can never satisfy it.
    const drawerBadge = await portal.expectDrawerKevBadge();
    expect(drawerBadge.dueDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(DUE_STATES).toContain(drawerBadge.dueState);
  });
});
