/**
 * ScansQueueHarness — `/scans` global queue page domain verbs.
 *
 * Sibling of {@link AdminScansHarness}. The user-facing `/scans` page
 * shows the per-team scan queue with status tabs (Running / Queued /
 * Failed / Completed). The admin-side `/admin/scans` is a different
 * surface and stays in `AdminScansHarness`.
 *
 * Bootstrap scope: mount + row count + tab switching, sufficient for
 * the screenshot capture pipeline. E2E mutation coverage lives in the
 * existing scan-flow spec.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type ScansTab = "running" | "queued" | "succeeded" | "failed" | "all";

export class ScansQueueHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async gotoScans(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/scans`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("scans-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // The table + footer wrappers always render (skeleton, rows, or empty
    // cell — they all live inside the same table). Asserting on those keeps
    // the predicate immune to the empty-tbody zero-height race that the
    // earlier `tbody OR empty-cell` fallback hit during capture runs.
    await expect(this.page.getByTestId("scans-table")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("scans-pagination")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until the initial fetch settles so screenshots do not capture
    // the loading skeleton. `aria-busy` flips off once SWR resolves.
    await expect
      .poll(() => this.page.getByTestId("scans-table").getAttribute("aria-busy"), {
        timeout: DEFAULT_TIMEOUT_MS,
      })
      .not.toBe("true");
  }

  // ───── tabs ────────────────────────────────────────────────────────────
  /**
   * Switch to a status tab and wait for the table to settle. Useful for
   * the screenshot pipeline where the default `running` tab is empty
   * against a `withScan: true` seed (which produces succeeded scans).
   */
  async selectTab(tab: ScansTab): Promise<void> {
    const btn = this.page.getByTestId(`scans-tab-${tab}`);
    await expect(btn).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await btn.click();
    await this.expectMounted();
  }

  // ───── list state ─────────────────────────────────────────────────────
  async getRowCount(): Promise<number> {
    return this.page.getByTestId("scans-row").count();
  }

  async expectEmpty(): Promise<void> {
    await expect(this.page.getByTestId("scans-empty")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }
}
