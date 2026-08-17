/**
 * The representative screen set — one entry per layout template.
 *
 * Two gates walk the same screens: the visual baselines
 * (`tests/visual/visual.spec.ts`) and the accessibility scan
 * (`tests/a11y/a11y.spec.ts`). They live here rather than being listed
 * twice, because a screen added to one and forgotten in the other is the
 * drift CLAUDE.md hardening rule #2 is about — each spec would stay green
 * on its own while the pair quietly disagreed about what "covered" means.
 *
 * Which screens earn a slot, and why each is here, is recorded in
 * `tests/visual/coverage-manifest.ts`; `visualCoverage.test.ts` holds the
 * manifest against what `router.tsx` actually mounts and against the ids
 * in `screenIds.ts`.
 *
 * The `Record<AuthenticatedScreenId, …>` below is load-bearing: adding an
 * id without a navigation function is a type error, so the two halves
 * cannot drift apart.
 */
import type { Page } from "@playwright/test";

import { AdminUsersHarness } from "./AdminUsersHarness";
import { ApprovalsHarness } from "./ApprovalsHarness";
import { PortalPage } from "./PortalPage";
import {
  AUTHENTICATED_SCREEN_IDS,
  type AuthenticatedScreenId,
} from "./screenIds";

export interface ScreenContext {
  /** Seeded project the detail screens hang off. */
  projectId: string;
}

export type VisitScreen = (page: Page, ctx: ScreenContext) => Promise<void>;

export interface RepresentativeScreen {
  /** Stable id — also the visual baseline's file stem. */
  id: AuthenticatedScreenId;
  /** Navigate and wait until the surface is genuinely settled. */
  visit: VisitScreen;
}

const VISITS: Record<AuthenticatedScreenId, VisitScreen> = {
  "projects-list": async (page) => {
    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.expectProjectListVisible();
  },
  "project-detail-overview": async (page, { projectId }) => {
    await page.goto(`/projects/${projectId}`);
    const portal = new PortalPage(page);
    await portal.expectProjectDetailMounted();
    // The tab body loads on its own query. Without this the caller measures a
    // skeleton, or the frame mid-transition out of one (#89).
    await portal.expectOverviewTabReady();
  },
  "project-detail-vulnerabilities": async (page, { projectId }) => {
    await page.goto(`/projects/${projectId}?tab=vulnerabilities`);
    const portal = new PortalPage(page);
    await portal.expectProjectDetailMounted();
    // Not just "the list is on screen". The rendered window has to have
    // stopped moving. The container appears before Virtuoso has measured its
    // rows, and a capture taken during that pass differs from one taken after
    // it by a few pixels at the bottom edge (#114).
    await portal.expectVulnerabilityWindowSettled();
  },
  dashboard: async (page) => {
    await page.goto("/");
    await page
      .getByTestId("dashboard-severity-card")
      .waitFor({ state: "visible" });
    // The trend panel resolves on its own request. Without this wait the
    // capture races it, and whichever of skeleton or chart wins becomes the
    // baseline — the kind of drift a pixel gate is supposed to prevent
    // rather than record. Waiting for the panel (not for either panel or
    // its error card) also means a broken endpoint fails the run loudly
    // instead of quietly baselining an error state.
    await page.getByTestId("trends-panel").waitFor({ state: "visible" });
    await page.getByTestId("portfolio-grid").waitFor({ state: "visible" });
  },
  scans: async (page) => {
    await page.goto("/scans");
    await page
      .getByTestId("scans-status-badge")
      .first()
      .waitFor({ state: "visible" });
  },
  // These two waited for `PortalPage.expectMounted()`, which is the
  // authenticated SHELL and says nothing about the queue or the user table
  // inside it. Both screens already own a verb that waits for their own first
  // fetch to settle (`aria-busy` leaving the table), and neither gate was
  // calling it: the narrow gate captured approvals before a single figure had
  // rendered and failed with "no numeric text node was found, so the widened
  // pass asserted nothing" on a pull request whose diff was one backend
  // module (#151). Same shape as #114 one screen over.
  approvals: async (page) => {
    await page.goto("/approvals");
    await new ApprovalsHarness(page).expectMounted();
  },
  "admin-users": async (page) => {
    await page.goto("/admin/users");
    await new AdminUsersHarness(page).expectMounted();
  },
};

/**
 * Screens behind authentication. The pre-auth login page is handled
 * separately by each spec — it needs a cleared auth state rather than a
 * seeded one, so folding it in here would mean every consumer carrying a
 * special case.
 */
export const AUTHENTICATED_SCREENS: RepresentativeScreen[] =
  AUTHENTICATED_SCREEN_IDS.map((id) => ({ id, visit: VISITS[id] }));
