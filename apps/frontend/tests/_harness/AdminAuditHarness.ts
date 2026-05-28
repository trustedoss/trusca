/**
 * AdminAuditHarness — Phase 4 PR #14 §4.7.
 */
import { expect, type Download, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type AdminAuditSuccessKey = "csv_started";
export type AdminAuditErrorExtension = "audit_export_too_large";

export class AdminAuditHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/admin/audit`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("admin-layout")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-audit-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-audit-table")
            .getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  async expectAccessDenied(): Promise<void> {
    await expect(this.page.getByTestId("admin-not-found")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-layout")).toHaveCount(0);
  }

  // ───── filters ─────────────────────────────────────────────────────────
  async filterByTargetTable(value: string): Promise<void> {
    await this.page
      .getByTestId("admin-audit-target-table")
      .selectOption(value);
    await this.expectMounted();
  }

  async filterByAction(action: string): Promise<void> {
    await this.page.getByTestId("admin-audit-action").fill(action);
    // Action input has no debounce — wait for the table aria-busy round-trip.
    await this.expectMounted();
  }

  async searchDiff(query: string): Promise<void> {
    await this.page.getByTestId("admin-audit-q").fill(query);
    // 300ms debounce — wait for aria-busy to settle.
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-audit-table")
            .getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  // ───── rows ────────────────────────────────────────────────────────────
  async expectRowVisible(): Promise<void> {
    await expect(
      this.page.getByTestId("admin-audit-row").first(),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async getRowCount(): Promise<number> {
    return this.page.getByTestId("admin-audit-row").count();
  }

  async openFirstRowDrawer(): Promise<void> {
    const row = this.page.getByTestId("admin-audit-row").first();
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await row.click();
    await expect(this.page.getByTestId("admin-audit-drawer")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async closeDrawer(): Promise<void> {
    await this.page.keyboard.press("Escape");
    await expect(this.page.getByTestId("admin-audit-drawer")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── CSV export ──────────────────────────────────────────────────────
  /**
   * Click the export button and wait for the `download` event. Returns the
   * Download handle so the caller can inspect filename / read body.
   */
  async exportCsv(): Promise<Download> {
    const downloadPromise = this.page.waitForEvent("download", {
      timeout: 15_000,
    });
    await this.page.getByTestId("admin-audit-export-csv").click();
    return downloadPromise;
  }

  async expectSuccessToast(key: AdminAuditSuccessKey): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="success"][data-toast-key="${key}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async expectErrorAlert(extension: AdminAuditErrorExtension): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="error"][data-toast-key="${extension}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }
}
