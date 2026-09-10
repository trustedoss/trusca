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
 *
 * `A11Y_ONLY_SCREENS` (#425) is the one deliberate exception to "both gates
 * walk the same screens": it is a superset the accessibility scan alone
 * walks, on top of `AUTHENTICATED_SCREENS`. See `screenIds.ts` for why axe
 * coverage and the visual-baseline set are allowed to diverge here.
 */
import type { Page } from "@playwright/test";

import { AdminUsersHarness } from "./AdminUsersHarness";
import { ApprovalsHarness } from "./ApprovalsHarness";
import { PortalPage } from "./PortalPage";
import {
  A11Y_ONLY_SCREEN_IDS,
  AUTHENTICATED_SCREEN_IDS,
  type A11yOnlyScreenId,
  type AuthenticatedScreenId,
} from "./screenIds";

export interface ScreenContext {
  /** Seeded project the detail screens hang off. */
  projectId: string;
  /**
   * Seeded root group id (#425): `seed.team_id` is reused as a group id
   * post-rename (group-hierarchy PR 0-1), so the a11y-only `group-detail`
   * screen can address a real group without a second seed. Optional: only
   * `group-detail` reads it, and the visual/narrow-viewport gates' contexts
   * never carry one since they only walk `AUTHENTICATED_SCREENS`.
   */
  groupId?: string;
}

export type VisitScreen = (page: Page, ctx: ScreenContext) => Promise<void>;

export interface RepresentativeScreen {
  /**
   * Stable id. For an `AuthenticatedScreenId` it also doubles as the visual
   * baseline's file stem; an `A11yOnlyScreenId` carries no baseline (see
   * `screenIds.ts`).
   */
  id: AuthenticatedScreenId | A11yOnlyScreenId;
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

/**
 * Wait past the query that gates a screen's content, using the same
 * `aria-busy` convention `ApprovalsHarness.expectMounted()` already polls
 * (#151/#114's lesson: the shell mounting is not the data arriving). A
 * shared helper rather than a `Harness` class per new screen (#425)
 * because every one of these is a single element with a single query, and
 * the Harness-First principle earns its weight once a screen has several
 * verbs to expose, which none of these do yet.
 */
async function waitSettled(page: Page, testId: string): Promise<void> {
  const target = page.getByTestId(testId);
  await target.waitFor({ state: "visible" });
  await page.waitForFunction((id) => {
    const node = document.querySelector(`[data-testid="${id}"]`);
    return node?.getAttribute("aria-busy") !== "true";
  }, testId);
}

const A11Y_ONLY_VISITS: Record<A11yOnlyScreenId, VisitScreen> = {
  "project-create": async (page) => {
    await page.goto("/projects/new");
    await page.getByTestId("project-create-form").waitFor({ state: "visible" });
  },
  search: async (page) => {
    await page.goto("/search");
    // No query yet, so there is no fetch to settle on: the tab bar is the
    // whole of this screen's chrome until someone types something.
    await page.getByTestId("search-tabs").waitFor({ state: "visible" });
  },
  inventory: async (page) => {
    await page.goto("/components");
    // No `aria-busy` here (unlike the pages below), so wait for whichever of
    // the two mutually exclusive post-load states the seeded portfolio
    // produces, matching the same "loading vs. settled" distinction the
    // aria-busy poll makes elsewhere in this file.
    await Promise.race([
      page.getByTestId("inventory-table").waitFor({ state: "visible" }),
      page.getByTestId("inventory-empty").waitFor({ state: "visible" }),
    ]);
  },
  groups: async (page) => {
    await page.goto("/groups");
    await waitSettled(page, "groups-table");
  },
  "group-detail": async (page, { groupId }) => {
    if (!groupId) throw new Error("group-detail visit needs ScreenContext.groupId");
    await page.goto(`/groups/${groupId}`);
    // No `aria-busy` on this screen; `group-detail-tabs` only renders past
    // the dedicated `group-detail-loading` branch.
    await page.getByTestId("group-detail-tabs").waitFor({ state: "visible" });
  },
  policies: async (page) => {
    await page.goto("/policies");
    await waitSettled(page, "policies-table");
  },
  integrations: async (page) => {
    await page.goto("/integrations");
    await waitSettled(page, "integrations-keys-table");
  },
  profile: async (page) => {
    await page.goto("/profile");
    await waitSettled(page, "profile-identities-list");
  },
  about: async (page) => {
    await page.goto("/about");
    // Past `about-loading`'s skeleton: `about-product` only paints once the
    // notice bundle has resolved.
    await page.getByTestId("about-product").waitFor({ state: "visible" });
  },
  "admin-teams": async (page) => {
    await page.goto("/admin/teams");
    await waitSettled(page, "admin-teams-table");
  },
  "admin-scans": async (page) => {
    await page.goto("/admin/scans");
    await waitSettled(page, "admin-scans-table");
  },
  "admin-audit": async (page) => {
    await page.goto("/admin/audit");
    await waitSettled(page, "admin-audit-table");
  },
  "external-package-lookup": async (page) => {
    await page.goto("/packages/lookup");
    // Deployment-gated (same flag family as IntakeRequestsPage): either the
    // lookup form or the disabled notice is the screen, depending on the
    // stack this runs against.
    await Promise.race([
      page.getByTestId("external-package-lookup-form").waitFor({ state: "visible" }),
      page.getByTestId("external-package-lookup-disabled").waitFor({ state: "visible" }),
    ]);
  },
};

export const A11Y_ONLY_SCREENS: RepresentativeScreen[] =
  A11Y_ONLY_SCREEN_IDS.map((id) => ({ id, visit: A11Y_ONLY_VISITS[id] }));
