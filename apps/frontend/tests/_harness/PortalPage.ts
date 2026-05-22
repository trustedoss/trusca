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

import { AdminAuditHarness } from "./AdminAuditHarness";
import { AdminDiskHarness } from "./AdminDiskHarness";
import { AdminDTHarness } from "./AdminDTHarness";
import { AdminHealthHarness } from "./AdminHealthHarness";
import { AdminScansHarness } from "./AdminScansHarness";
import { AdminTeamsHarness } from "./AdminTeamsHarness";
import { AdminUsersHarness } from "./AdminUsersHarness";

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
    // AppShell sidebar is the reliable "authenticated shell loaded" sentinel.
    // The old `home-main` no longer exists — `/` redirects to `/projects`.
    await this.page
      .getByTestId("app-sidebar")
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
   * equals `projectName`, then drive the source-select dialog (PR #91) to
   * actually start the scan. Uses the row's button so the test does not
   * depend on visual ordering of the virtualized list.
   *
   * `method` defaults to "git" — the seed project carries a git_url, so the
   * dialog can submit without attaching a file. Pass "upload"/"folder" only
   * for tests that exercise those input paths (they must stage a file first).
   */
  async clickTriggerScan(
    projectName: string,
    method: "git" | "upload" | "folder" = "git",
  ): Promise<void> {
    await this.page
      .locator(`[data-testid="project-row-scan"][data-project-name="${projectName}"]`)
      .click();
    // PR #91: the scan button opens the source-select dialog first.
    await this.page
      .getByTestId("source-select-dialog")
      .waitFor({ state: "visible", timeout: 10_000 });
    await this.page.getByTestId(`source-method-${method}`).click();
    await this.page.getByTestId("source-submit").click();
  }

  /** Open the source-select dialog without submitting (input-path tests). */
  async openSourceSelectDialog(projectName: string): Promise<void> {
    await this.page
      .locator(`[data-testid="project-row-scan"][data-project-name="${projectName}"]`)
      .click();
    await this.page
      .getByTestId("source-select-dialog")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /**
   * Drive the source-select dialog's "upload .zip" path end to end:
   * select the upload method, stage an in-memory `.zip` (so the test never
   * touches the host filesystem), wait for the submit button to enable, then
   * submit. The dialog requires a `.zip` (the file picker rejects other
   * extensions client-side), so callers pass a tiny valid zip payload.
   *
   * `zip` defaults to a minimal empty-zip byte sequence (the PK end-of-central-
   * directory record), which is enough for the SPA's client-side `.zip`
   * extension guard + multipart upload. The backend's archive extractor may
   * reject a zip with no entries — for tests that need the worker to actually
   * process the upload, pass a real fixture zip's bytes.
   *
   * Must be called from the projects list (it opens the row's dialog itself).
   */
  async startScanByUpload(
    projectName: string,
    zip?: { name?: string; bytes?: Uint8Array },
  ): Promise<void> {
    await this.openSourceSelectDialog(projectName);
    await this.page.getByTestId("source-method-upload").click();
    await this.attachScanZip(zip);
    // The submit button is disabled until a valid file is staged — wait for
    // it to enable (event-driven), then submit.
    const submit = this.page.getByTestId("source-submit");
    await expect(submit).toBeEnabled({ timeout: 10_000 });
    await submit.click();
  }

  /**
   * Stage a `.zip` on the dialog's hidden `<input type=file>` using an
   * in-memory buffer so no temp file is written to the host. Mirrors the
   * shape Playwright's `setInputFiles({ name, mimeType, buffer })` expects.
   */
  async attachScanZip(zip?: {
    name?: string;
    bytes?: Uint8Array;
  }): Promise<void> {
    const name = zip?.name ?? "source.zip";
    // Minimal valid empty-zip: just the End Of Central Directory record.
    const bytes =
      zip?.bytes ??
      new Uint8Array([
        0x50, 0x4b, 0x05, 0x06, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
      ]);
    await this.page.getByTestId("source-zip-input").setInputFiles({
      name,
      mimeType: "application/zip",
      buffer: Buffer.from(bytes),
    });
    // The dialog echoes the staged file name once accepted.
    await this.page
      .getByTestId("source-upload-selected")
      .waitFor({ state: "visible", timeout: 10_000 });
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
   * Click the first vulnerability row (whatever it happens to be) and wait
   * for the drawer to mount. Sibling of {@link openVulnerabilityDrawer} for
   * scenarios that don't care which CVE — e.g. screenshot capture, where
   * the seeded CVE ids are timestamped and the spec only needs *some*
   * drawer open. Anchors on the `data-testid="vulnerability-row"` attribute
   * (locale-agnostic) and waits for the URL to mirror `?vuln=<finding_id>`.
   */
  async openFirstVulnerabilityDrawer(): Promise<void> {
    const row = this.page.getByTestId("vulnerability-row").first();
    await expect(row).toBeVisible({ timeout: 10_000 });
    await row.click();
    await this.page
      .getByTestId("vulnerability-drawer")
      .waitFor({ state: "visible", timeout: 10_000 });
    await expect
      .poll(() => new URL(this.page.url()).searchParams.get("vuln"), {
        timeout: 5_000,
      })
      .not.toBeNull();
    // Also wait for the Analysis section to mount — the screenshot caller
    // depends on the VEX action buttons being visible, which only render
    // once the detail query resolves (the drawer body is a skeleton until
    // then).
    await this.page
      .getByTestId("vulnerability-drawer-analysis")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  // ───── PR #12 — Licenses tab + drawer ────────────────────────────────
  /**
   * Click the Licenses tab trigger and wait for the tab content to mount.
   * Mirrors `selectVulnerabilitiesTab`: the tab renders
   * `[data-testid="licenses-tab"]` once the React subtree mounts; we also
   * wait until either the virtual list or the empty card is visible so
   * subsequent verbs (filter / open drawer) have a settled DOM to target.
   *
   * Locale-agnostic — anchors on `data-testid` attributes, never the
   * translated tab label.
   */
  async selectLicensesTab(): Promise<void> {
    await this.page.getByTestId("project-detail-tab-licenses").click();
    await this.page
      .getByTestId("licenses-tab")
      .waitFor({ state: "visible", timeout: 10_000 });
    await this.expectLicensesTabReady();
  }

  /**
   * Wait until either the virtualized list or the empty card is visible
   * (the loading skeleton has finished). Use after applying filters or
   * sorts to wait for the next page to land. Event-driven — never
   * `waitForTimeout`.
   */
  async expectLicensesTabReady(): Promise<void> {
    const virtual = this.page.getByTestId("licenses-virtual");
    const empty = this.page.getByTestId("licenses-empty");
    await expect(virtual.or(empty)).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Set the multi-select category filter to exactly the given categories.
   * An empty array clears the filter. The toolbar uses a native
   * `<select multiple>` so Playwright's `selectOption` handles it cleanly,
   * matching the vulnerabilities-severity verb pattern.
   *
   * After mutating the filter the harness waits for the URL to mirror the
   * change (`?license_category=…`) so callers can read the URL deterministically.
   */
  async filterLicensesByCategory(
    categories: ("forbidden" | "conditional" | "allowed" | "unknown")[],
  ): Promise<void> {
    await this.page
      .getByTestId("licenses-category-filter")
      .selectOption(categories);
    // URL mirrors the filter as a CSV under `license_category`.
    if (categories.length > 0) {
      await expect
        .poll(
          () =>
            (
              new URL(this.page.url()).searchParams.get("license_category") ??
              ""
            )
              .split(",")
              .filter((v) => v.length > 0)
              .sort()
              .join(","),
          { timeout: 5_000 },
        )
        .toBe([...categories].sort().join(","));
    } else {
      await expect
        .poll(
          () =>
            new URL(this.page.url()).searchParams.get("license_category"),
          { timeout: 5_000 },
        )
        .toBeNull();
    }
    await this.expectLicensesTabReady();
  }

  /**
   * Set the multi-select kind filter (declared / concluded / detected).
   * Mirrors `filterLicensesByCategory`.
   */
  async filterLicensesByKind(
    kinds: ("declared" | "concluded" | "detected")[],
  ): Promise<void> {
    await this.page
      .getByTestId("licenses-kind-filter")
      .selectOption(kinds);
    await this.expectLicensesTabReady();
  }

  /**
   * Find the row whose `data-spdx-id` equals `spdxId` and click it. Wait
   * for the drawer to mount (URL carries `?license=<finding_id>` and the
   * drawer container is visible).
   *
   * Locale-agnostic — anchors on the `data-spdx-id` attribute the row
   * exposes verbatim. ORT custom licenses (LicenseRef-*) without an SPDX
   * id are out of scope for this verb; callers that need them should
   * target the row's `data-finding-id` directly.
   */
  async openLicenseDrawer(spdxId: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="license-row"][data-spdx-id="${spdxId}"]`,
    );
    await row.first().click();
    await this.page
      .getByTestId("license-drawer")
      .waitFor({ state: "visible", timeout: 10_000 });
    await expect
      .poll(() => new URL(this.page.url()).searchParams.get("license"), {
        timeout: 5_000,
      })
      .not.toBeNull();
  }

  /**
   * Read the licenses-summary `data-total` attribute (server-reported count).
   * Returns 0 when the empty card is shown.
   */
  async getLicenseRowCount(): Promise<number> {
    const summary = this.page.getByTestId("licenses-summary");
    if ((await summary.count()) === 0) return 0;
    const raw = await summary.first().getAttribute("data-total");
    return raw == null ? 0 : Number(raw);
  }

  /**
   * Phase 3 PR #13 — Obligations tab harness verbs.
   *
   * Mirrors the licenses-tab verbs: select / wait-ready / multi-filter / row
   * → drawer / read summary count. Plus a `downloadNotice` verb that wraps
   * `page.waitForEvent('download')` so callers can assert the file name +
   * MIME without rolling their own download plumbing per spec.
   */
  async selectObligationsTab(): Promise<void> {
    await this.page.getByTestId("project-detail-tab-obligations").click();
    await this.page
      .getByTestId("obligations-tab")
      .waitFor({ state: "visible", timeout: 10_000 });
    await this.expectObligationsTabReady();
  }

  async expectObligationsTabReady(): Promise<void> {
    const virtual = this.page.getByTestId("obligations-virtual");
    const empty = this.page.getByTestId("obligations-empty");
    await expect(virtual.or(empty)).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Switch to the project-detail SBOM tab and wait for it to settle. The
   * SBOM tab renders a format selector + download list rooted at
   * `data-testid="sbom-tab"`. When no scan exists yet the tab body shows
   * `sbom-no-scan`; both are valid mounted states for screenshots.
   */
  async selectSbomTab(): Promise<void> {
    await this.page.getByTestId("project-detail-tab-sbom").click();
    await this.expectSbomTabReady();
  }

  async expectSbomTabReady(): Promise<void> {
    const tab = this.page.getByTestId("sbom-tab");
    await expect(tab).toBeVisible({ timeout: 10_000 });
    await expect(
      this.page
        .getByTestId("sbom-last-scan")
        .or(this.page.getByTestId("sbom-no-scan")),
    ).toBeVisible({ timeout: 10_000 });
  }

  async filterObligationsByKind(kinds: string[]): Promise<void> {
    await this.page
      .getByTestId("obligations-kind-filter")
      .selectOption(kinds);
    if (kinds.length > 0) {
      await expect
        .poll(
          () =>
            (new URL(this.page.url()).searchParams.get("kind") ?? "")
              .split(",")
              .filter((v) => v.length > 0)
              .sort()
              .join(","),
          { timeout: 5_000 },
        )
        .toBe([...kinds].sort().join(","));
    } else {
      await expect
        .poll(() => new URL(this.page.url()).searchParams.get("kind"), {
          timeout: 5_000,
        })
        .toBeNull();
    }
    await this.expectObligationsTabReady();
  }

  /**
   * Open the obligation drawer for the row whose `data-obligation-id`
   * matches. The list endpoint returns ids verbatim so the spec can pick
   * the first row's id and pass it back here for a deterministic open.
   */
  async openObligationDrawer(obligationId: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="obligation-row"][data-obligation-id="${obligationId}"]`,
    );
    await row.first().click();
    await this.page
      .getByTestId("obligation-drawer")
      .waitFor({ state: "visible", timeout: 10_000 });
    await expect
      .poll(() => new URL(this.page.url()).searchParams.get("obligation"), {
        timeout: 5_000,
      })
      .not.toBeNull();
  }

  async getObligationRowCount(): Promise<number> {
    const summary = this.page.getByTestId("obligations-summary");
    if ((await summary.count()) === 0) return 0;
    const raw = await summary.first().getAttribute("data-total");
    return raw == null ? 0 : Number(raw);
  }

  /**
   * Click the NOTICE download button and wait for the browser download
   * event. Returns `{ filename, body }` so callers can assert provenance
   * without snooping the response stream themselves.
   *
   * `format` drives the toolbar's format `<select>` (`data-testid=
   * obligations-notice-format`) before clicking download — the UI exposes
   * `text` + `html` (markdown stays API-only, see NOTICE_DOWNLOAD_FORMATS).
   * Passing "markdown" falls through to the same select; if the option is not
   * present Playwright's `selectOption` throws, surfacing the contract drift
   * rather than silently downloading text. Defaults to "text" so existing
   * callers stay source-compatible.
   */
  async downloadNotice(
    format: "text" | "markdown" | "html" = "text",
  ): Promise<{ filename: string; body: string }> {
    // The format select defaults to "text"; only touch it when the caller
    // asked for something else so the default path stays a single click.
    if (format !== "text") {
      await this.page
        .getByTestId("obligations-notice-format")
        .selectOption(format);
    }
    const downloadPromise = this.page.waitForEvent("download", {
      timeout: 15_000,
    });
    await this.page.getByTestId("obligations-download-notice").click();
    const download = await downloadPromise;
    return readDownload(download);
  }

  // ───── G3.3 — Source file-tree viewer (Source tab) ─────────────────────
  /**
   * Click the Source tab trigger and wait for the tab body to mount. The tab
   * renders `[data-testid="source-tab"]` in both the populated (two-pane) and
   * the "no preserved source" empty states, so its visibility is the
   * synchronization signal. Inside, the harness waits until the tree's first
   * level resolves — either rows mounted, the empty-source card mounted, or
   * the tree error alert mounted — so subsequent verbs target a settled DOM.
   *
   * Locale-agnostic — anchors on `data-testid` attributes, never the
   * translated tab label.
   */
  async selectSourceTab(): Promise<void> {
    await this.page.getByTestId("project-detail-tab-source").click();
    await this.page
      .getByTestId("source-tab")
      .waitFor({ state: "visible", timeout: 10_000 });
    await this.expectSourceTreeReady();
  }

  /**
   * Wait until the source tree's root level has settled into one of its
   * terminal states. The tree mounts a loading skeleton first; we wait until
   * one of {first row, empty-dir note, no-preserved-source card, tree error}
   * is visible. Event-driven — never `waitForTimeout`.
   */
  async expectSourceTreeReady(): Promise<void> {
    const firstRow = this.page.getByTestId("source-tree-row").first();
    const noSource = this.page.getByTestId("source-no-preserved");
    const treeError = this.page.getByTestId("source-tree-error");
    await expect(firstRow.or(noSource).or(treeError)).toBeVisible({
      timeout: 10_000,
    });
  }

  /**
   * Assert the "no preserved source" empty state is showing. Old scans (or
   * the current e2e seed, which does not stage a source tarball) return a 404
   * for the tree root, which the tab swaps for this single card instead of an
   * error toast.
   */
  async expectSourceEmptyState(): Promise<void> {
    await this.page
      .getByTestId("source-no-preserved")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /**
   * Expand the directory tree node whose `data-path` equals `dirPath`. The
   * row is a `<button role=treeitem>` carrying `data-is-dir="true"`; clicking
   * toggles `data-expanded`. The harness waits until the row reports
   * `data-expanded="true"` and the child level (`source-tree-level` /
   * `…-level-virtual` keyed by `data-dir=dirPath`) mounts, so callers can
   * immediately target deeper rows. Lazy disclosure means the child level's
   * own network call resolves before its rows appear — we wait on the level
   * wrapper, then on its readiness, both event-driven.
   *
   * Throws (via auto-retry) if the path is not a directory row.
   */
  async expandSourceTreeNode(dirPath: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="source-tree-row"][data-path="${cssEscapeAttr(dirPath)}"][data-is-dir="true"]`,
    );
    await expect(row.first()).toBeVisible({ timeout: 10_000 });
    // Toggle open only if currently collapsed (idempotent).
    if ((await row.first().getAttribute("data-expanded")) !== "true") {
      await row.first().click();
    }
    await expect(row.first()).toHaveAttribute("data-expanded", "true", {
      timeout: 10_000,
    });
    // The child level mounts keyed by data-dir; wait for either the plain or
    // the virtualized wrapper to appear so deeper-row queries are stable.
    const level = this.page.locator(
      `[data-dir="${cssEscapeAttr(dirPath)}"]`,
    );
    await expect(level.first()).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Click the file tree row whose `data-path` equals `filePath` (a leaf,
   * `data-is-dir="false"`) and wait for the viewer to render that file. The
   * viewer mirrors the path to `?path=` and renders
   * `[data-testid="source-file-viewer"][data-path=…]` once the file query
   * resolves. The harness waits for both the URL mirror and the viewer's
   * `data-path` to converge on `filePath`, so the per-line assertions below
   * read a settled DOM. Binary / truncated / not-found terminal states are
   * still inside `source-file-viewer`'s siblings and have their own verbs.
   */
  async openSourceFile(filePath: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="source-tree-row"][data-path="${cssEscapeAttr(filePath)}"][data-is-dir="false"]`,
    );
    await expect(row.first()).toBeVisible({ timeout: 10_000 });
    await row.first().click();
    // URL mirrors the selection (?path=…).
    await expect
      .poll(() => new URL(this.page.url()).searchParams.get("path"), {
        timeout: 5_000,
      })
      .toBe(filePath);
    // The viewer resolves to one of: content viewer, binary note, not-found,
    // or error. Wait until the loading skeleton clears (any terminal mounts).
    await this.expectSourceFileSettled();
  }

  /**
   * Wait until the file viewer pane has left its loading skeleton — one of
   * {content viewer, binary note, not-found card, error alert} is visible.
   * Event-driven.
   */
  async expectSourceFileSettled(): Promise<void> {
    const viewer = this.page.getByTestId("source-file-viewer");
    const binary = this.page.getByTestId("source-file-binary");
    const notFound = this.page.getByTestId("source-file-not-found");
    const error = this.page.getByTestId("source-file-error");
    await expect(
      viewer.or(binary).or(notFound).or(error),
    ).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Assert the viewer is showing a decoded text file (utf-8) — the content
   * pane (`source-file-content`) is mounted and the viewer's `data-encoding`
   * is "utf-8". Returns once both hold.
   */
  async expectSourceFileText(): Promise<void> {
    const viewer = this.page.getByTestId("source-file-viewer");
    await expect(viewer).toBeVisible({ timeout: 10_000 });
    await expect(viewer).toHaveAttribute("data-encoding", "utf-8", {
      timeout: 10_000,
    });
    await expect(this.page.getByTestId("source-file-content")).toBeVisible({
      timeout: 10_000,
    });
  }

  /** Assert the viewer rendered the binary-file notice (we never show bytes). */
  async expectSourceFileBinary(): Promise<void> {
    await this.page
      .getByTestId("source-file-binary")
      .waitFor({ state: "visible", timeout: 10_000 });
  }

  /**
   * Assert the viewer rendered the truncated banner + download button (the
   * file exceeded the per-file byte cap). Optionally returns nothing — pair
   * with {@link downloadTruncatedSourceFile} to exercise the download.
   */
  async expectSourceFileTruncated(): Promise<void> {
    const viewer = this.page.getByTestId("source-file-viewer");
    await expect(viewer).toHaveAttribute("data-truncated", "true", {
      timeout: 10_000,
    });
    await expect(
      this.page.getByTestId("source-file-truncated-banner"),
    ).toBeVisible({ timeout: 10_000 });
  }

  /**
   * Assert that the line numbered `lineNumber` (1-based) carries a license
   * match. The line row exposes `data-highlighted="true"` and a sibling
   * license chip (`source-line-license-chip`) whose `data-spdx-ids` lists the
   * matched SPDX ids (CSV). When `expectedSpdxId` is given, the harness
   * additionally asserts it appears in that CSV — locale-agnostic, reads the
   * attribute not the rendered label.
   *
   * NOTE: lines virtualize through react-virtuoso, so a line far down a long
   * file may not be mounted until scrolled into view. Use this for lines in
   * the first viewport (the seeded fixtures keep matches near the top); a
   * `scrollSourceLineIntoView` verb can be added if deeper assertions are
   * needed.
   */
  async expectSourceLineLicense(
    lineNumber: number,
    expectedSpdxId?: string,
  ): Promise<void> {
    const line = this.page.locator(
      `[data-testid="source-line"][data-line="${lineNumber}"]`,
    );
    await expect(line.first()).toBeVisible({ timeout: 10_000 });
    await expect(line.first()).toHaveAttribute("data-highlighted", "true", {
      timeout: 10_000,
    });
    const chip = line
      .first()
      .locator('[data-testid="source-line-license-chip"]');
    await expect(chip).toBeVisible({ timeout: 10_000 });
    if (expectedSpdxId !== undefined) {
      const raw = (await chip.getAttribute("data-spdx-ids")) ?? "";
      const ids = raw.split(",").map((s) => s.trim());
      expect(ids).toContain(expectedSpdxId);
    }
  }

  /**
   * Assert the line numbered `lineNumber` is NOT highlighted (no license
   * match). Companion of {@link expectSourceLineLicense} so a spec can show
   * the per-line panel is selective, not a blanket tint.
   */
  async expectSourceLineUnmatched(lineNumber: number): Promise<void> {
    const line = this.page.locator(
      `[data-testid="source-line"][data-line="${lineNumber}"]`,
    );
    await expect(line.first()).toBeVisible({ timeout: 10_000 });
    await expect(line.first()).toHaveAttribute(
      "data-highlighted",
      "false",
      { timeout: 10_000 },
    );
  }

  /**
   * Click the truncated-file download button and capture the resulting
   * browser download. Returns `{ filename, body }` so the spec can assert the
   * downloaded slice matches the bytes that were rendered. Throws (via
   * auto-retry on the button) when the file is not truncated.
   */
  async downloadTruncatedSourceFile(): Promise<{
    filename: string;
    body: string;
  }> {
    const downloadPromise = this.page.waitForEvent("download", {
      timeout: 15_000,
    });
    await this.page.getByTestId("source-file-download").click();
    const download = await downloadPromise;
    return readDownload(download);
  }

  // ───── G2 — Vulnerability PDF report download ──────────────────────────
  /**
   * Click the "Download PDF report" button on the Vulnerabilities toolbar and
   * wait for the browser download event. Returns `{ filename, body }` — the
   * body is the raw PDF bytes as a binary string so the caller can assert the
   * `%PDF-` magic header without rolling its own stream plumbing.
   *
   * Must be called with the Vulnerabilities tab mounted (the button lives in
   * its toolbar). The button disables itself while generating; the harness
   * does not need to poll the disabled state because the download event only
   * fires once the blob is ready.
   *
   * Locale-agnostic — anchors on `data-testid="vuln-download-pdf"`.
   */
  async downloadVulnReportPdf(): Promise<{ filename: string; body: string }> {
    const downloadPromise = this.page.waitForEvent("download", {
      timeout: 20_000,
    });
    await this.page.getByTestId("vuln-download-pdf").click();
    const download = await downloadPromise;
    // PDFs are binary — read as latin1 so the %PDF- magic + %%EOF trailer
    // survive byte-for-byte for the magic-header assertion.
    return readDownload(download, "latin1");
  }

  /**
   * Assert the PDF download surfaced an inline error (e.g. the project has no
   * succeeded scan). The toolbar renders `vuln-download-pdf-error` with the
   * problem detail; the harness only asserts presence so the spec stays
   * locale-agnostic.
   */
  async expectVulnReportPdfError(): Promise<void> {
    await this.page
      .getByTestId("vuln-download-pdf-error")
      .waitFor({ state: "visible", timeout: 10_000 });
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

  // ───── PR #13 — Admin panel (Phase 4) ──────────────────────────────────
  /**
   * Navigate to ``/admin/users`` and return a domain-verb harness for the
   * page. Convenience wrapper so spec files don't have to import the admin
   * harnesses themselves; the underlying class is still available for tests
   * that need to construct it directly (e.g. "expectAccessDenied" assertions
   * that don't want the auto-mount wait).
   */
  async gotoAdminUsers(): Promise<AdminUsersHarness> {
    const harness = new AdminUsersHarness(this.page, this.baseUrl);
    await harness.goto();
    return harness;
  }

  /** Sibling of {@link gotoAdminUsers} for the ``/admin/teams`` surface. */
  async gotoAdminTeams(): Promise<AdminTeamsHarness> {
    const harness = new AdminTeamsHarness(this.page, this.baseUrl);
    await harness.goto();
    return harness;
  }

  // ───── PR #14 — Admin operational dashboards (DT/Scans/Disk/Audit/Health)
  /** Navigate to ``/admin/dt`` and return the {@link AdminDTHarness}. */
  async gotoAdminDT(): Promise<AdminDTHarness> {
    const harness = new AdminDTHarness(this.page, this.baseUrl);
    await harness.goto();
    return harness;
  }

  /** Navigate to ``/admin/scans`` and return the {@link AdminScansHarness}. */
  async gotoAdminScans(): Promise<AdminScansHarness> {
    const harness = new AdminScansHarness(this.page, this.baseUrl);
    await harness.goto();
    return harness;
  }

  /** Navigate to ``/admin/disk`` and return the {@link AdminDiskHarness}. */
  async gotoAdminDisk(): Promise<AdminDiskHarness> {
    const harness = new AdminDiskHarness(this.page, this.baseUrl);
    await harness.goto();
    return harness;
  }

  /** Navigate to ``/admin/audit`` and return the {@link AdminAuditHarness}. */
  async gotoAdminAudit(): Promise<AdminAuditHarness> {
    const harness = new AdminAuditHarness(this.page, this.baseUrl);
    await harness.goto();
    return harness;
  }

  /** Navigate to ``/admin/health`` and return the {@link AdminHealthHarness}. */
  async gotoAdminHealth(): Promise<AdminHealthHarness> {
    const harness = new AdminHealthHarness(this.page, this.baseUrl);
    await harness.goto();
    return harness;
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

/**
 * Read a Playwright `Download` into memory. Prefers the on-disk path (set
 * when downloads are saved) and falls back to the stream. `encoding` selects
 * the decoding: 'utf-8' for text artifacts (NOTICE), 'latin1' for binary ones
 * (PDF) so the bytes round-trip 1:1 for magic-header assertions.
 *
 * Shared by `downloadNotice`, `downloadVulnReportPdf`, and
 * `downloadTruncatedSourceFile` so the download plumbing lives in exactly one
 * place — spec files only see `{ filename, body }`.
 */
async function readDownload(
  download: import("@playwright/test").Download,
  encoding: BufferEncoding = "utf-8",
): Promise<{ filename: string; body: string }> {
  const fs = await import("node:fs/promises");
  const onDisk = await download.path();
  let body: string;
  if (onDisk) {
    const buf = await fs.readFile(onDisk);
    body = buf.toString(encoding);
  } else {
    const stream = await download.createReadStream();
    if (!stream) {
      body = "";
    } else {
      const chunks: Buffer[] = [];
      for await (const chunk of stream) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      }
      body = Buffer.concat(chunks).toString(encoding);
    }
  }
  return { filename: download.suggestedFilename(), body };
}

/**
 * Escape a `data-*` attribute value for embedding inside a CSS attribute
 * selector. Source paths can contain quotes/backslashes; we escape `\` and
 * `"` so `[data-path="<value>"]` stays well-formed. POSIX source paths in the
 * preserved tree never contain newlines, so this minimal escape is sufficient.
 */
function cssEscapeAttr(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}
