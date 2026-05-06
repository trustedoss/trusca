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
import { expect, type Locator, type Page } from "@playwright/test";

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

  // ───── PR #10 — Project Detail (task 3.1 / 3.3) ────────────────────────
  /**
   * Click the project name link inside the row whose `data-project-name`
   * equals `projectName` and wait until the detail page is mounted.
   *
   * Project rows render two `data-testid="project-row-link"` siblings only if
   * the same project appears twice; we anchor on the first one whose `text`
   * matches the seeded name to stay deterministic when multiple projects
   * share a similar prefix.
   */
  async openProjectDetail(projectName: string): Promise<void> {
    // The link carries `data-project-id` only — the seeded `projectName` is
    // the visible text. Anchoring by visible text would couple the harness
    // to translation keys, so we target the row's `data-project-name` on
    // the sibling Scan button to find the row, then click the row's link.
    const row = this.page.locator(
      `[data-testid="project-row"]:has([data-testid="project-row-scan"][data-project-name="${projectName}"])`,
    );
    await row.locator('[data-testid="project-row-link"]').click();
    await this.expectProjectDetailMounted();
  }

  /** Assert the project detail page is mounted (any tab). */
  async expectProjectDetailMounted(): Promise<void> {
    await this.page
      .getByTestId("project-detail-page")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /**
   * Switch to one of the four detail tabs. The detail page's
   * `?tab=…` URL mirroring is asserted in scenarios that care.
   */
  async selectTab(
    tabName: "overview" | "components" | "vulnerabilities" | "licenses",
  ): Promise<void> {
    await this.page
      .getByTestId(`project-detail-tab-${tabName}`)
      .click();
  }

  /**
   * Wait until the components tab's network call resolves. The tab renders
   * `[data-testid=components-virtual]` only after the first page lands, so
   * the absence of that node is the synchronization signal — far more
   * reliable than waiting for a specific row count.
   */
  async expectComponentsTabReady(): Promise<void> {
    // Either the virtual list mounted (rows arrived) or the empty card
    // mounted (zero rows for the current filter set). Both are valid
    // "tab finished loading" states.
    const virtual = this.page.getByTestId("components-virtual");
    const empty = this.page.getByTestId("components-empty");
    await expect(virtual.or(empty)).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Set the multi-select severity filter to exactly the given severities.
   * An empty array clears the filter. Backed by a native `<select multiple>`
   * — Playwright's `selectOption` semantics handle the multi-select cleanly.
   */
  async filterComponentsBySeverity(
    severities: ("critical" | "high" | "medium" | "low" | "info" | "none")[],
  ): Promise<void> {
    await this.page
      .getByTestId("components-severity-filter")
      .selectOption(severities);
    await this.expectComponentsTabReady();
  }

  /**
   * Type into the components search input. The toolbar debounces by 300ms
   * before mutating the URL + firing the next page request — callers that
   * assert on row count should use `expectComponentsTabReady()` afterwards.
   *
   * Empty string clears the filter.
   */
  async searchComponents(query: string): Promise<void> {
    const input = this.page.getByTestId("components-search");
    await input.fill(query);
    // Wait for the debounce to fire and the URL to reflect the new query.
    // We watch for `?search=…` rather than waitForTimeout — auto-retrying
    // and locale-agnostic.
    if (query.length > 0) {
      await expect
        .poll(() => new URL(this.page.url()).searchParams.get("search"), {
          timeout: 5_000,
        })
        .toBe(query);
    } else {
      await expect
        .poll(() => new URL(this.page.url()).searchParams.get("search"), {
          timeout: 5_000,
        })
        .toBeNull();
    }
    await this.expectComponentsTabReady();
  }

  /**
   * Click the row whose visible name matches `componentName` and wait for
   * the drawer to mount. Anchors on the row's truncated `<span>` text — the
   * row carries no `data-component-name`, but the seeded names are unique
   * per scan so a strict equality match is safe.
   */
  async openComponentDrawer(componentName: string): Promise<void> {
    const row = this.page
      .getByTestId("component-row")
      .filter({ hasText: componentName })
      .first();
    await row.click();
    await this.page
      .getByTestId("component-drawer")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /**
   * Assert the Overview tab's risk gauge reads `expected` ± `tolerance`.
   * The default tolerance is 1 — the backend computes the score from a
   * weighted sum that's deterministic given the seed, but rounds to an
   * int for display. Callers can pass `{ tolerance: 0 }` for an exact match.
   */
  async assertRiskScore(
    expected: number,
    options: { tolerance?: number } = {},
  ): Promise<void> {
    const tolerance = options.tolerance ?? 1;
    const gauge = this.page.getByTestId("risk-gauge");
    await expect(gauge).toBeVisible({ timeout: 10_000 });
    // The numeric is exposed via a `data-score` attribute so we can assert
    // without hitting the rendered text (locale-agnostic).
    await expect
      .poll(
        async () => {
          const raw = await gauge.getAttribute("data-score");
          return raw == null ? Number.NaN : Number(raw);
        },
        { timeout: 10_000 },
      )
      .toBeGreaterThanOrEqual(expected - tolerance);
    const score = Number(await gauge.getAttribute("data-score"));
    expect(score).toBeLessThanOrEqual(expected + tolerance);
  }

  /**
   * Read the components-virtual `data-loaded` attribute (loaded row count).
   * Returns 0 when the virtual list is not mounted (empty state).
   */
  async getLoadedComponentCount(): Promise<number> {
    const virtual = this.page.getByTestId("components-virtual");
    if ((await virtual.count()) === 0) return 0;
    const raw = await virtual.first().getAttribute("data-loaded");
    return raw == null ? 0 : Number(raw);
  }

  /**
   * Read the components-virtual `data-total` attribute (server-reported
   * total row count). Returns 0 when the empty card is shown.
   */
  async getTotalComponentCount(): Promise<number> {
    const virtual = this.page.getByTestId("components-virtual");
    if ((await virtual.count()) === 0) return 0;
    const raw = await virtual.first().getAttribute("data-total");
    return raw == null ? 0 : Number(raw);
  }

  /**
   * Trigger Virtuoso's `endReached` until the loaded count stops growing or
   * we hit `maxIterations`. We dispatch a wheel event over the virtual list
   * — `mouse.wheel` requires the cursor to be over the scroll container,
   * which Virtuoso renders inside the `[data-testid=components-virtual]`
   * wrapper.
   */
  async scrollComponentsToLoadMore(maxIterations: number = 8): Promise<number> {
    const virtual = this.page.getByTestId("components-virtual");
    await expect(virtual).toBeVisible();
    const box = await virtual.boundingBox();
    if (!box) return this.getLoadedComponentCount();

    let lastLoaded = await this.getLoadedComponentCount();
    for (let i = 0; i < maxIterations; i++) {
      await this.page.mouse.move(
        box.x + box.width / 2,
        box.y + box.height - 10,
      );
      await this.page.mouse.wheel(0, 4_000);
      // Wait for either the loaded count to grow or the network to settle.
      try {
        await expect
          .poll(() => this.getLoadedComponentCount(), { timeout: 2_500 })
          .toBeGreaterThan(lastLoaded);
      } catch {
        // No new rows arrived in this tick — accept and stop scrolling.
        break;
      }
      lastLoaded = await this.getLoadedComponentCount();
    }
    return lastLoaded;
  }

  /**
   * Pick a sort key on the components toolbar. Values map to the
   * `ComponentSortKey` enum in `projectDetailApi.ts` ('name' | 'severity'
   * | 'license').
   */
  async sortComponentsBy(
    key: "name" | "severity" | "license",
  ): Promise<void> {
    await this.page.getByTestId("components-sort").selectOption(key);
    await this.expectComponentsTabReady();
  }

  /** Pick a sort order — 'asc' | 'desc'. */
  async setComponentsOrder(order: "asc" | "desc"): Promise<void> {
    await this.page.getByTestId("components-order").selectOption(order);
    await this.expectComponentsTabReady();
  }

  /**
   * Read the severity of the n-th row's SeverityBadge. The badge surfaces
   * its 6-bucket value verbatim via `data-severity` ('critical' | 'high' |
   * 'medium' | 'low' | 'info' | 'none'), so this is locale-agnostic.
   * Throws if no row at that index is mounted.
   */
  async getRowSeverity(index: number): Promise<string | null> {
    const row = this.page.getByTestId("component-row").nth(index);
    await expect(row).toBeVisible({ timeout: 10_000 });
    return row.locator("[data-severity]").first().getAttribute("data-severity");
  }

  // ───── PR #11 — Vulnerabilities tab + drawer ───────────────────────────
  /**
   * Click the Vulnerabilities tab trigger and wait for the tab content to
   * mount. The tab renders `[data-testid="vulnerabilities-tab"]` once
   * mounted; the loading skeleton is a sibling, so `vulnerabilities-tab`
   * being visible is the synchronization signal.
   *
   * Locale-agnostic: anchors on `data-testid` attributes rather than the
   * translated tab label.
   */
  async selectVulnerabilitiesTab(): Promise<void> {
    await this.page
      .getByTestId("project-detail-tab-vulnerabilities")
      .click();
    await this.page
      .getByTestId("vulnerabilities-tab")
      .waitFor({ state: "visible", timeout: 10_000 });
    // After the tab mounts, either the empty card, the virtual list, or the
    // loading skeleton is visible — wait until one of the data states
    // resolves so subsequent verbs can click rows reliably.
    await this.expectVulnerabilitiesTabReady();
  }

  /**
   * Wait until either the virtualized list or the empty card is visible
   * (the loading skeleton has finished). Use after applying filters /
   * sorts to wait for the next page to land.
   */
  async expectVulnerabilitiesTabReady(): Promise<void> {
    const virtual = this.page.getByTestId("vulnerabilities-virtual");
    const empty = this.page.getByTestId("vulnerabilities-empty");
    await expect(virtual.or(empty)).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Read the `data-total` attribute on the summary row (server-reported
   * count). Returns 0 when the empty card is shown.
   */
  async getVulnerabilityRowCount(): Promise<number> {
    const summary = this.page.getByTestId("vulnerabilities-summary");
    if ((await summary.count()) === 0) return 0;
    const raw = await summary.first().getAttribute("data-total");
    return raw == null ? 0 : Number(raw);
  }

  /** Set the multi-select severity filter. Empty array clears it. */
  async filterVulnerabilitiesBySeverity(
    severities: ("critical" | "high" | "medium" | "low" | "info" | "unknown")[],
  ): Promise<void> {
    await this.page
      .getByTestId("vulnerabilities-severity-filter")
      .selectOption(severities);
    await this.expectVulnerabilitiesTabReady();
  }

  /** Set the multi-select status filter. */
  async filterVulnerabilitiesByStatus(
    statuses: VulnFindingStatus[],
  ): Promise<void> {
    await this.page
      .getByTestId("vulnerabilities-status-filter")
      .selectOption(statuses);
    await this.expectVulnerabilitiesTabReady();
  }

  /**
   * Find the row whose `data-cve-id` equals `cveId` and click it. Wait
   * for the drawer to open (URL carries `?vuln=<finding_id>` and the
   * drawer container is visible).
   *
   * Locale-agnostic: anchors on the `data-cve-id` attribute the row
   * exposes verbatim.
   */
  async openVulnerabilityDrawer(cveId: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="vulnerability-row"][data-cve-id="${cveId}"]`,
    );
    await row.first().click();
    await this.page
      .getByTestId("vulnerability-drawer")
      .waitFor({ state: "visible", timeout: 10_000 });
    // URL mirrors the selection — wait until ?vuln=<...> appears.
    await expect
      .poll(() => new URL(this.page.url()).searchParams.get("vuln"), {
        timeout: 5_000,
      })
      .not.toBeNull();
  }

  /**
   * Drive a status transition from inside the open drawer.
   *
   * Optionally fills the justification textarea, then clicks the action
   * button matching `targetStatus`. Waits until the drawer's status badge
   * reflects the new value (event-driven via `expect.poll`; never
   * `waitForTimeout`).
   *
   * Throws via Playwright's auto-retrying assertions if the button is
   * disabled (role-gated) or the post-mutation badge never updates.
   */
  async setVulnerabilityStatus(
    targetStatus: VulnFindingStatus,
    justification?: string,
  ): Promise<void> {
    if (justification !== undefined) {
      await this.page
        .getByTestId("vulnerability-drawer-justification")
        .fill(justification);
    }
    await this.page
      .getByTestId(`vulnerability-drawer-action-${targetStatus}`)
      .click();
    // The status badge inside the drawer carries `data-status`; wait until
    // it flips to the target value (or stays put on error — caller can
    // inspect the alert separately).
    await expect
      .poll(
        async () => {
          const badge = this.page
            .getByTestId("vulnerability-drawer-meta")
            .locator(`[data-testid^="vulnerability-status-badge-"]`)
            .first();
          if ((await badge.count()) === 0) return null;
          return badge.getAttribute("data-status");
        },
        { timeout: 10_000 },
      )
      .toBe(targetStatus);
  }
}

/** CycloneDX VEX status union — mirrors the backend ENUM. */
export type VulnFindingStatus =
  | "new"
  | "analyzing"
  | "exploitable"
  | "not_affected"
  | "false_positive"
  | "suppressed"
  | "fixed";

function assertSupported(value: string | null): SupportedLanguage {
  if (value && (SUPPORTED_LANGUAGES as readonly string[]).includes(value)) {
    return value as SupportedLanguage;
  }
  throw new Error(`Unsupported language attribute: ${String(value)}`);
}
