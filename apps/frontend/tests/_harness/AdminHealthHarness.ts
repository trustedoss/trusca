/**
 * AdminHealthHarness — Phase 4 PR #14 §4.8.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type HealthComponentName =
  | "postgres"
  | "redis"
  | "celery"
  | "dt"
  | "disk"
  | "active_scans"
  | "last_24h_errors";

export type HealthStatus = "ok" | "degraded" | "down";

export class AdminHealthHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/admin/health`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("admin-layout")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-health-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until at least one component card mounts.
    await expect(
      this.page.getByTestId("admin-health-card").first(),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async expectAccessDenied(): Promise<void> {
    await expect(this.page.getByTestId("admin-not-found")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-layout")).toHaveCount(0);
  }

  /** Read the per-component status via ``data-status``. */
  async getComponentStatus(
    name: HealthComponentName,
  ): Promise<HealthStatus | null> {
    const value = await this.page
      .locator(`[data-testid="admin-health-card"][data-component="${name}"]`)
      .getAttribute("data-status");
    return value === null ? null : (value as HealthStatus);
  }

  async getComponentNames(): Promise<HealthComponentName[]> {
    const names: HealthComponentName[] = [];
    const cards = this.page.getByTestId("admin-health-card");
    const count = await cards.count();
    for (let i = 0; i < count; i++) {
      const value = await cards.nth(i).getAttribute("data-component");
      if (value !== null) names.push(value as HealthComponentName);
    }
    return names;
  }

  async refresh(): Promise<void> {
    await this.page.getByTestId("admin-health-refresh").click();
    await this.expectMounted();
  }
}
