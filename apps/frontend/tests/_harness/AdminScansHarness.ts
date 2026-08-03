/**
 * AdminScansHarness — Phase 4 PR #14 §4.5.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type AdminScanStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";
export type AdminScansTab = "running" | "queued" | "failed" | "all";
export type AdminScanSuccessKey = "cancelled";
export type AdminScanErrorExtension =
  | "scan_already_cancelled"
  | "scan_not_found";

export class AdminScansHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/admin/scans`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("admin-layout")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-scans-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-scans-table")
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

  // ───── tabs ────────────────────────────────────────────────────────────
  async selectTab(tab: AdminScansTab): Promise<void> {
    await this.page.getByTestId(`admin-scans-tab-${tab}`).click();
    await this.expectMounted();
  }

  async getRowCount(): Promise<number> {
    const rows = this.page.getByTestId("admin-scans-row");
    return rows.count();
  }

  // ───── row → drawer ────────────────────────────────────────────────────
  async openScanDrawer(scanId: string): Promise<void> {
    await this.page
      .locator(
        `[data-testid="admin-scans-row"][data-scan-id="${cssEscape(scanId)}"]`,
      )
      .click();
    await expect(this.page.getByTestId("admin-scan-drawer")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async openFirstRowDrawer(): Promise<string | null> {
    const row = this.page.getByTestId("admin-scans-row").first();
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const id = await row.getAttribute("data-scan-id");
    await row.click();
    await expect(this.page.getByTestId("admin-scan-drawer")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    return id;
  }

  async closeDrawer(): Promise<void> {
    await this.page.keyboard.press("Escape");
    await expect(this.page.getByTestId("admin-scan-drawer")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── cancel flow ─────────────────────────────────────────────────────
  async cancelOpenScan(): Promise<void> {
    await this.page.getByTestId("admin-scan-action-cancel").click();
    await expect(
      this.page.getByTestId("admin-scan-confirm-strip"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await this.page.getByTestId("admin-scan-confirm-ok").click();
  }

  // ───── status assertions ───────────────────────────────────────────────
  async getRowStatus(scanId: string): Promise<AdminScanStatus | null> {
    const value = await this.page
      .locator(
        `[data-testid="admin-scans-row"][data-scan-id="${cssEscape(scanId)}"]`,
      )
      .getAttribute("data-status");
    return value === null ? null : (value as AdminScanStatus);
  }

  async expectSuccessToast(key: AdminScanSuccessKey): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="success"][data-toast-key="${key}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async expectErrorAlert(extension: AdminScanErrorExtension): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="error"][data-toast-key="${extension}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }
}

function cssEscape(value: string): string {
  return value.replace(/(["\\])/g, "\\$1");
}
