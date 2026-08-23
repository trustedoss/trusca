/**
 * InventoryHarness — organization-wide component inventory (`/components`, S2).
 *
 * Sibling of ApprovalsHarness / AdminUsersHarness. Hard rules it inherits from
 * CLAUDE.md:
 *   - no mocking the backend from a spec; drive the real dev stack
 *   - no `waitForTimeout`; wait on a condition
 *   - selectors live HERE, never in a spec file
 *
 * Covers: page mount, the summary counts, row assertions by package name, the
 * search / package-type / lifecycle filters, and the "used by" drawer.
 */
import { expect, type Locator, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export class InventoryHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────

  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/components`);
    await this.expectMounted();
  }

  /** The sidebar entry, so a spec can prove the page is reachable by nav. */
  async gotoViaSidebar(): Promise<void> {
    await this.page.getByTestId("nav-components").click();
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("inventory-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // The summary strip renders as soon as the first page resolves, so it is
    // the honest "done loading" signal — waiting on the table alone would pass
    // while the skeleton is still up.
    await expect(this.page.getByTestId("inventory-summary")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("inventory-loading")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── list state ──────────────────────────────────────────────────────

  /** Total packages the server reports, independent of how many are rendered. */
  async getTotal(): Promise<number> {
    const raw = await this.page
      .getByTestId("inventory-summary")
      .getAttribute("data-total");
    return Number.parseInt(raw ?? "0", 10);
  }

  rows(): Locator {
    return this.page.getByTestId("inventory-row");
  }

  row(packageName: string): Locator {
    return this.rows().filter({ hasText: packageName });
  }

  /** How many projects the inventory says use *packageName*. */
  async getProjectCount(packageName: string): Promise<number> {
    const raw = await this.row(packageName)
      .first()
      .getAttribute("data-project-count");
    return Number.parseInt(raw ?? "0", 10);
  }

  async getVersionCount(packageName: string): Promise<number> {
    const raw = await this.row(packageName)
      .first()
      .getAttribute("data-version-count");
    return Number.parseInt(raw ?? "0", 10);
  }

  async expectRowVisible(packageName: string): Promise<void> {
    await expect(this.row(packageName).first()).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async expectRowAbsent(packageName: string): Promise<void> {
    await expect(this.row(packageName)).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── filters ─────────────────────────────────────────────────────────

  /**
   * Type into the search box and wait for the debounced term to reach the URL,
   * then for the refetch to settle.
   */
  async search(term: string): Promise<void> {
    await this.page.getByTestId("inventory-search").fill(term);
    await expect
      .poll(
        () => new URL(this.page.url()).searchParams.get("inv_search"),
        { timeout: 5_000 },
      )
      .toBe(term.length > 0 ? term : null);
    await this.expectMounted();
  }

  // ───── empty state ─────────────────────────────────────────────────────
  //
  // Concurrency-scaling plan Q2 (2026-08-22) removed the "Search every scan"
  // link this page used to offer on a fruitless search: search narrowed to
  // the same latest-scan-only scope this page already used, so the link
  // would always land on another empty result. No harness verb replaces it.

  // ───── drawer ──────────────────────────────────────────────────────────

  /** Click a package row and wait for the "used by" drawer. */
  async openUsage(packageName: string): Promise<void> {
    await this.row(packageName).first().click();
    await expect(this.page.getByTestId("inventory-drawer")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("inventory-drawer-list")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  drawerRows(): Locator {
    return this.page.getByTestId("inventory-drawer-row");
  }

  async getDrawerProjectNames(): Promise<string[]> {
    const rows = await this.drawerRows().all();
    const names: string[] = [];
    for (const row of rows) {
      names.push(((await row.textContent()) ?? "").trim());
    }
    return names;
  }
}
