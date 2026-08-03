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

  // ───── Phase C — CISA KEV feed panel ────────────────────────────────────
  //
  // Locale-agnostic anchors: the panel root carries
  // `data-testid="kev-feed-panel"` + `data-status`
  // (empty/disabled/skipped/synced — absent while the loading skeleton is
  // up), the never-ran branch renders `kev-feed-empty`, the status badge is
  // `kev-feed-status-badge` (+ `data-status`), and each KPI tile exposes the
  // raw wire value on `data-value`. No verb reads translated copy.

  /**
   * Wait for the KEV feed panel to mount AND settle (loading skeleton gone:
   * either the never-ran EmptyState, a KPI tile, or the error alert is
   * visible), then return its resolved shape. `status` is the panel root's
   * `data-status`; `empty` flags the never-ran EmptyState; `error` flags the
   * fetch-failed alert — spec files decide which combinations are legal.
   */
  async expectKevFeedPanel(): Promise<{
    status: string | null;
    empty: boolean;
    error: boolean;
  }> {
    const panel = this.page.getByTestId("kev-feed-panel");
    await expect(panel).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const empty = this.page.getByTestId("kev-feed-empty");
    const kpi = this.page.getByTestId("kev-feed-kpi-last-synced");
    const error = this.page.getByTestId("kev-feed-error");
    await expect(empty.or(kpi).or(error)).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    return {
      status: await panel.getAttribute("data-status"),
      empty: (await empty.count()) > 0,
      error: (await error.count()) > 0,
    };
  }

  /**
   * Read the KEV status badge's `data-status` (`disabled` / `skipped` /
   * `synced`), or `null` when no badge renders (the enabled never-ran
   * branch carries the message via the EmptyState alone).
   */
  async getKevFeedBadgeStatus(): Promise<string | null> {
    const badge = this.page.getByTestId("kev-feed-status-badge");
    if ((await badge.count()) === 0) return null;
    return badge.getAttribute("data-status");
  }

  /**
   * Read a KEV KPI tile's raw wire value (`data-value`), or `null` when the
   * tile is absent (never-ran branch) or the run left the metric null.
   */
  async getKevKpiValue(
    kpi: "last-synced" | "flagged-total" | "listed-delisted" | "next-refresh",
  ): Promise<string | null> {
    const tile = this.page.getByTestId(`kev-feed-kpi-${kpi}`);
    if ((await tile.count()) === 0) return null;
    return tile.getAttribute("data-value");
  }
}
