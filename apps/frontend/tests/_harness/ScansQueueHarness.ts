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
    // Either the table body or the empty card must mount before screenshots.
    await expect(
      this.page
        .getByTestId("scans-tbody")
        .or(this.page.getByTestId("scans-empty")),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
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
