/**
 * docs-uat ui dispatcher — the generic "docs ARE the tests" spec.
 *
 * Reads the docs-uat manifest produced by `tools/docs-uat/extract.mjs`,
 * filters the ui-kind steps for the doc + tier the runner asked for
 * (DOCS_UAT_DOC / DOCS_UAT_TIER / DOCS_UAT_LANG), and replays each step's
 * `harness=verb(args)` annotation against the EXISTING harness verbs
 * (design decision D5 — reuse, never reimplement). No new UI assertion logic
 * lives here; this file is only the binding from annotation → verb.
 *
 * The steps run in document order inside a single test on one shared page,
 * so the `login` step establishes the session the later navigation/assertion
 * steps depend on (one login keeps us under the 5/min/IP rate limit).
 *
 * To teach docs-uat a new ui verb, add it to VERBS below (or, for a brand-new
 * screen, add the verb to PortalPage first per the harness-first rule).
 */
import * as fs from "node:fs";

import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";

interface Step {
  id: string;
  kind: string;
  tier: string;
  lang: string;
  doc: string;
  line: number;
  harness?: string;
  waiver?: string;
}

const MANIFEST = process.env.DOCS_UAT_MANIFEST;
const DOC = process.env.DOCS_UAT_DOC;
const TIER = process.env.DOCS_UAT_TIER ?? "gate";
const LANG = process.env.DOCS_UAT_LANG ?? "en";

function loadUiSteps(): Step[] {
  if (!MANIFEST || !DOC) {
    throw new Error("docs-uat spec requires DOCS_UAT_MANIFEST + DOCS_UAT_DOC env");
  }
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, "utf8"));
  return (manifest.steps as Step[])
    .filter(
      (s) =>
        s.kind === "ui" &&
        s.doc === DOC &&
        s.lang === LANG &&
        s.tier === TIER &&
        !s.waiver,
    )
    .sort((a, b) => a.line - b.line);
}

/** `expectVisibleProjectCount(5)` → { name, args: ["5"] }. */
function parseVerb(harness: string): { name: string; args: string[] } {
  const m = harness.match(/^([A-Za-z0-9_]+)(?:\((.*)\))?$/);
  if (!m) throw new Error(`docs-uat: unparseable harness verb "${harness}"`);
  const args = m[2] ? m[2].split(",").map((a) => a.trim()) : [];
  return { name: m[1], args };
}

interface Ctx {
  auth: AuthHarness;
  portal: PortalPage;
}

/** Annotation verb → existing harness call. */
const VERBS: Record<string, (ctx: Ctx, args: string[]) => Promise<void>> = {
  async login({ auth }, [email, password]) {
    await auth.gotoLogin();
    await auth.login(email, password);
  },
  async expectMounted({ portal }) {
    await portal.expectMounted();
  },
  async expectVisibleProjectCount({ portal }, [count]) {
    await portal.gotoProjects();
    await portal.expectVisibleProjectCount(Number(count));
  },
  // Phase C — user-guide UI. Composite verbs that thread existing PortalPage
  // navigation + assertion methods (no new PortalPage verbs); they assert the
  // doc's "Verify it worked" claim against the seeded project's real data.
  async componentsHaveData({ portal }, [projectName]) {
    await portal.gotoProjects();
    await portal.openProjectDetail(projectName);
    await portal.selectTab("components");
    await portal.expectComponentsTabReady();
    const total = await portal.getTotalComponentCount();
    expect(total, `${projectName} component count`).toBeGreaterThan(0);
  },
  async licensesGridPopulated({ portal }, [projectName]) {
    await portal.gotoProjects();
    await portal.openProjectDetail(projectName);
    // Licenses were absorbed into the unified Compliance grid (W4-C IA);
    // selectLicensesTab() clicks Compliance, shows the licenses-first view, and
    // waits for it to be ready (there is no `licenses` top-level tab anymore).
    await portal.selectLicensesTab();
    // The doc's "forbidden licenses highlighted in red" is a visual claim
    // (covered by the manual step); the automatable core is that the licenses
    // grid is populated for the seeded project. Read the unified Compliance
    // grid's own `compliance-summary` data-total — the legacy `licenses-summary`
    // testid that getLicenseRowCount reads is stale post-W4-C (always 0 here).
    const total = Number(
      (await portal.page
        .getByTestId("compliance-summary")
        .first()
        .getAttribute("data-total")) ?? "0",
    );
    expect(total, `${projectName} compliance grid rows`).toBeGreaterThan(0);
  },
  // dashboard.md — the portfolio landing page is this screen's sole automated
  // coverage (no standalone e2e spec exists). Composite verbs over
  // PortalPage.goto + the dashboard testids; each re-navigates to `/` so the
  // steps are order-independent on the shared page.
  async dashboardActiveInNav({ portal }) {
    await portal.goto("/");
    await expect(portal.page.getByTestId("dashboard-page")).toBeVisible();
    // react-router's NavLink sets aria-current="page" on the active entry.
    await expect(portal.page.getByTestId("nav-dashboard")).toHaveAttribute(
      "aria-current",
      "page",
    );
  },
  async dashboardSeverityTiles({ portal }) {
    await portal.goto("/");
    await expect(
      portal.page.getByTestId("dashboard-severity-card"),
    ).toBeVisible();
  },
  async dashboardRecentScans({ portal }) {
    await portal.goto("/");
    // The recent-scans card renders either a populated table or the
    // documented empty-state — assert one of the two is present.
    const table = portal.page.getByTestId("dashboard-recent-scans-table");
    const empty = portal.page.getByTestId("dashboard-recent-scans-empty");
    await expect(table.or(empty)).toBeVisible();
  },
  // scans.md — the global scan queue + the post-scan project state.
  async scansListPopulated({ portal }) {
    await portal.goto("/scans");
    await expect(portal.page.getByTestId("scans-table")).toBeVisible();
    const rows = await portal.page.getByTestId("scans-row").count();
    expect(rows, "global scan queue rows").toBeGreaterThan(0);
  },
  async vulnerabilitiesTabReady({ portal }, [projectName]) {
    await portal.gotoProjects();
    await portal.openProjectDetail(projectName);
    await portal.selectVulnerabilitiesTab();
    await portal.expectVulnerabilitiesTabReady();
  },
  // auth-and-profile.md — after the spec's auto-login, the header surfaces the
  // signed-in identity via the profile link. OAuth + unlink stay manual.
  async headerProfileVisible({ portal }) {
    await portal.goto("/");
    await expect(portal.page.getByTestId("header-profile-link")).toBeVisible();
  },
};

const uiSteps = loadUiSteps();

test.describe(`docs-uat ui — ${DOC} (${TIER})`, () => {
  test("replays the documented ui steps", async ({ page, baseURL }) => {
    test.skip(uiSteps.length === 0, `no ${TIER}-tier ui steps for ${DOC}`);
    const ctx: Ctx = {
      auth: new AuthHarness(page, baseURL ?? undefined),
      portal: new PortalPage(page, baseURL ?? undefined),
    };
    // Quickstart documents its own sign-in (a `login` ui step); feature docs
    // (user-guide / admin-guide) assume you are already signed in. So when the
    // doc has no `login` step, establish a session as the demo super-admin
    // first — otherwise the first navigation redirects to /login and times out.
    const hasLogin = uiSteps.some(
      (s) => parseVerb(s.harness ?? "").name === "login",
    );
    if (!hasLogin) {
      await ctx.auth.gotoLogin();
      await ctx.auth.login(
        process.env.DOCS_UAT_ADMIN_EMAIL ?? "admin@demo.trustedoss.dev",
        process.env.DOCS_UAT_ADMIN_PASSWORD ?? "DemoTest2026!",
      );
    }
    for (const step of uiSteps) {
      const { name, args } = parseVerb(step.harness ?? "");
      const verb = VERBS[name];
      if (!verb) {
        throw new Error(
          `docs-uat [${step.id}]: no ui verb '${name}' registered ` +
            `(add it to docs-uat.spec.ts VERBS, or add the verb to PortalPage first)`,
        );
      }
      await test.step(`${step.id}: ${step.harness}`, async () => {
        await verb(ctx, args);
      });
    }
  });
});
