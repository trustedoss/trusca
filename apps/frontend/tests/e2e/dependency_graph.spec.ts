/**
 * Dependency graph view E2E — Phase H-1 (BomLens parity #9).
 *
 * The Components tab gains a table ↔ graph toggle backed by
 * `GET /projects/:id/dependency-graph`. The e2e seed now wires a binary-tree
 * dependency edge set over the seeded components (component_count − 1 edges), so
 * the cytoscape graph renders real edges — not just the edge-less fallback.
 *
 *   S1 — the toggle switches to the graph view, mirrors `?view=graph`, renders
 *        the dependency graph with the seeded node count, and survives reload;
 *        toggling back restores the table.
 *
 * Selectors are `data-testid` / `data-*` (EN/KO-agnostic). Auth uses the
 * refresh-cookie path so a full suite run stays under the 5/min login limiter.
 *
 * Pre-requisites (auto-skip otherwise):
 *   - docker-compose -f docker-compose.dev.yml up -d
 *   - python3 reachable from host PATH (the seed.ts harness validates this)
 */
import { expect, test } from "@playwright/test";

import { AuthHarness } from "../_harness/auth";
import { PortalPage } from "../_harness/PortalPage";
import { seedE2eUser, type SeedSummary } from "../_harness/seed";

const PROJECT_NAME = "ci-depgraph";
const COMPONENT_COUNT = 6; // → 5 binary-tree edges, so the graph has real edges.

function tryAcquireSeed(
  testInfo: import("@playwright/test").TestInfo,
  opts: Parameters<typeof seedE2eUser>[0],
): SeedSummary | null {
  try {
    return seedE2eUser(opts);
  } catch (err) {
    testInfo.skip(
      true,
      `seed precondition failed — bring docker-compose dev up + ensure ` +
        `python3 is on PATH: ${err instanceof Error ? err.message : String(err)}`,
    );
    return null;
  }
}

async function bootstrap(
  testInfo: import("@playwright/test").TestInfo,
  page: import("@playwright/test").Page,
): Promise<SeedSummary | null> {
  const seed = tryAcquireSeed(testInfo, {
    projectNames: [PROJECT_NAME],
    withScan: true,
    componentCount: COMPONENT_COUNT,
    componentPrefix: "dep",
    withRefreshToken: true,
  });
  if (seed === null) return null;

  const auth = new AuthHarness(page);
  await auth.loginViaRefreshCookie(seed.refresh_token!.token);
  return seed;
}

test.describe("@dependency-graph components graph view", () => {
  test.beforeEach(async ({ page }) => {
    const auth = new AuthHarness(page);
    await auth.clearAuthState();
  });

  test("S1) toggle renders the dependency graph and persists in the URL", async ({
    page,
  }, testInfo) => {
    const seed = await bootstrap(testInfo, page);
    if (seed === null) return;

    const portal = new PortalPage(page);
    await portal.gotoProjects();
    await portal.openProjectDetail(PROJECT_NAME);
    await portal.selectTab("components");

    // Default is the table view.
    await expect(page.getByTestId("components-view-toggle")).toBeVisible();
    await expect(
      page.getByTestId("components-view-toggle-table"),
    ).toHaveAttribute("data-active", "true");

    // Switch to the graph view.
    await page.getByTestId("components-view-toggle-graph").click();
    await expect(page.getByTestId("components-graph-view")).toBeVisible();
    expect(new URL(page.url()).searchParams.get("view")).toBe("graph");

    // The graph renders with the seeded node count (6 nodes, 5 edges) — the
    // cytoscape path, not the edge-less fallback. The sr-only node list mirrors
    // every node as a stable, canvas-independent test hook.
    const graph = page.getByTestId("dependency-graph");
    await expect(graph).toBeVisible();
    await expect(graph).toHaveAttribute("data-node-count", String(COMPONENT_COUNT));
    await expect(page.getByTestId("dependency-graph-node")).toHaveCount(
      COMPONENT_COUNT,
    );

    // The choice survives a hard reload (deep-link contract).
    await page.reload();
    await portal.selectTab("components");
    await expect(page.getByTestId("components-graph-view")).toBeVisible();
    expect(new URL(page.url()).searchParams.get("view")).toBe("graph");

    // Toggling back restores the table view.
    await page.getByTestId("components-view-toggle-table").click();
    await expect(page.getByTestId("components-graph-view")).toHaveCount(0);
    await expect(page.getByTestId("components-summary")).toBeVisible();
    expect(new URL(page.url()).searchParams.get("view")).not.toBe("graph");
  });
});
