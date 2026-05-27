/**
 * AdminDiskHarness — Phase 4 PR #14 §4.6.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type DiskCardName = "workspace" | "postgres" | "redis";
export type DiskHealthStatus = "ok" | "degraded" | "down";

export class AdminDiskHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/admin/disk`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("admin-layout")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-disk-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until at least one card mounts (skeletons are replaced).
    await expect(
      this.page.getByTestId("admin-disk-card").first(),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async expectAccessDenied(): Promise<void> {
    await expect(this.page.getByTestId("admin-not-found")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-layout")).toHaveCount(0);
  }

  async getCardStatus(name: DiskCardName): Promise<DiskHealthStatus | null> {
    const value = await this.page
      .locator(`[data-testid="admin-disk-card"][data-card-name="${name}"]`)
      .getAttribute("data-status");
    return value === null ? null : (value as DiskHealthStatus);
  }

  /**
   * Read the used percentage written into the progress bar's
   * ``data-used-pct`` attribute. Returns null when the card surfaced an
   * error path (no bar mounted).
   */
  async getCardUsedPct(name: DiskCardName): Promise<number | null> {
    const card = this.page.locator(
      `[data-testid="admin-disk-card"][data-card-name="${name}"]`,
    );
    const bar = card.locator('[data-testid="admin-disk-bar-fill"]');
    if ((await bar.count()) === 0) return null;
    const raw = await bar.getAttribute("data-used-pct");
    return raw === null ? null : Number(raw);
  }

  async refresh(): Promise<void> {
    await this.page.getByTestId("admin-disk-refresh").click();
    await this.expectMounted();
  }
}
