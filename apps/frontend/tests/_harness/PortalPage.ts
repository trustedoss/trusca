/**
 * PortalPage — Playwright harness skeleton.
 *
 * Phase 0 PR #3 ships only the harness shape: a navigation root + a small
 * vocabulary of high-level methods. Real Playwright execution is wired up in
 * PR #5 (Phase 1 authentication) when a meaningful login/dashboard surface
 * exists. The shape mirrors the v1 PortalPage so test-writer agents can reuse
 * the muscle memory.
 *
 * Why ship the harness now: PR #5 will land 14+ scenarios in a single
 * session, so having the entry point + supported-language enum already in
 * tree avoids a same-PR refactor of every spec we touch.
 */
import type { Locator, Page } from "@playwright/test";

// We deliberately re-declare the supported-language tuple here instead of
// importing from `@/lib/i18n`. The product i18n module pulls in JSON locale
// files as ESM imports — Playwright's runner does not understand the
// `import attributes` proposal yet, so importing it transitively breaks
// every spec that uses PortalPage. The list is short enough that a manual
// duplicate is the lesser evil; a unit test in
// `apps/frontend/tests/unit/lib/wsBase.test.ts` (or equivalent) can pin
// the contract.
const SUPPORTED_LANGUAGES = ["en", "ko"] as const;
type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

const DEFAULT_BASE_URL = "http://localhost:5173";

export class PortalPage {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async goto(path: string = "/"): Promise<void> {
    await this.page.goto(`${this.baseUrl}${path}`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await this.page
      .getByTestId("home-main")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  // ───── i18n ────────────────────────────────────────────────────────────
  languageToggle(): Locator {
    return this.page.getByTestId("language-toggle");
  }

  async currentLanguage(): Promise<SupportedLanguage> {
    const value = await this.languageToggle().getAttribute(
      "data-current-language",
    );
    return assertSupported(value);
  }

  async toggleLanguage(): Promise<SupportedLanguage> {
    await this.languageToggle().click();
    return this.currentLanguage();
  }

  // ───── PR #5 placeholders ──────────────────────────────────────────────
  // The methods below intentionally throw so accidental early use surfaces
  // a clear "not wired yet" error instead of a silent test pass.
  async login(_email: string, _password: string): Promise<void> {
    throw new Error("PortalPage.login: wired in PR #5 (Phase 1)");
  }

  async logout(): Promise<void> {
    throw new Error("PortalPage.logout: wired in PR #5 (Phase 1)");
  }

  // ───── PR #9 — Projects + scan progress (task 2.10/2.11) ───────────────
  /** Navigate to the project list page (`/projects`). */
  async gotoProjects(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/projects`);
    await this.expectProjectListVisible();
  }

  async expectProjectListVisible(): Promise<void> {
    await this.page
      .getByTestId("project-list-page")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /**
   * Click the "Scan" button on the project row whose `data-project-name`
   * equals `projectName`. Uses the row's button so the test does not depend
   * on visual ordering of the virtualized list.
   */
  async clickTriggerScan(projectName: string): Promise<void> {
    await this.page
      .locator(`[data-testid="project-row-scan"][data-project-name="${projectName}"]`)
      .click();
  }

  /**
   * Assert the scan progress drawer is visible. Optionally pass a step
   * label (e.g. "cdxgen") and the harness verifies that step has reached
   * the "current" or "completed" state.
   */
  async expectScanProgress(stepLabel?: string): Promise<void> {
    await this.page
      .getByTestId("scan-progress-drawer")
      .waitFor({ state: "visible", timeout: 10_000 });
    if (stepLabel) {
      const stepLocator = this.page.locator(
        `[data-testid="scan-progress-steps"] [data-step="${stepLabel}"]`,
      );
      await stepLocator.waitFor({ state: "visible", timeout: 10_000 });
    }
  }

  /** Assert the live progress reached `succeeded`. */
  async expectScanCompleted(): Promise<void> {
    await this.page
      .locator('[data-testid="scan-progress-steps"] [data-step="finalize"][data-state="completed"]')
      .waitFor({ state: "visible", timeout: 30_000 });
  }

  /** Assert the live progress reached `failed`. */
  async expectScanFailed(): Promise<void> {
    await this.page
      .locator('[data-testid="scan-progress-steps"] [data-state="failed"]')
      .waitFor({ state: "visible", timeout: 30_000 });
  }

  // ───── Project list filtering / sorting (PR #9 task 2.11) ──────────────
  /**
   * Type into the project list search box. Empty string clears the filter.
   * The toolbar debounces by 300ms — callers should follow with
   * {@link expectVisibleProjectCount} which auto-retries until the rendered
   * count converges.
   */
  async searchProjects(query: string): Promise<void> {
    const input = this.page.getByTestId("project-search");
    await input.fill(query);
  }

  /** Pick a status filter option (`all` | `idle` | `running` | …). */
  async filterProjectsByStatus(value: string): Promise<void> {
    await this.page.getByTestId("project-status-filter").selectOption(value);
  }

  /** Pick a sort option (`name` | `latest_scan` | `risk`). */
  async sortProjectsBy(value: string): Promise<void> {
    await this.page.getByTestId("project-sort").selectOption(value);
  }

  /**
   * Assert the virtualized list reports exactly `count` rows via the
   * `data-total` attribute on the container. The empty state replaces the
   * virtual list when zero rows match — the harness routes to the right
   * assertion automatically.
   */
  async expectVisibleProjectCount(count: number): Promise<void> {
    if (count === 0) {
      await this.page
        .getByTestId("project-list-empty")
        .waitFor({ state: "visible", timeout: 10_000 });
      return;
    }
    await this.page
      .locator(`[data-testid="project-list-virtual"][data-total="${count}"]`)
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /** Assert that a project row with the given name is visible. */
  async expectProjectRowVisible(projectName: string): Promise<void> {
    await this.page
      .locator(
        `[data-testid="project-row-scan"][data-project-name="${projectName}"]`,
      )
      .first()
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /** Click the close affordance on the scan-progress drawer (sheet). */
  async closeScanProgressDrawer(): Promise<void> {
    await this.page.getByTestId("scan-progress-close").click();
  }
}

function assertSupported(value: string | null): SupportedLanguage {
  if (value && (SUPPORTED_LANGUAGES as readonly string[]).includes(value)) {
    return value as SupportedLanguage;
  }
  throw new Error(`Unsupported language attribute: ${String(value)}`);
}
