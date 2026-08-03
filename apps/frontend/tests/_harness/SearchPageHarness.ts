// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * SearchPageHarness — the full search page (`/search`, S3).
 *
 * Sibling of InventoryHarness / ApprovalsHarness. Hard rules inherited from
 * CLAUDE.md: no mocking the backend from a spec, no `waitForTimeout`, and
 * selectors live HERE rather than in any spec file.
 *
 * Covers: the search box, the four kind tabs, facet chips, the result rows,
 * paging, and the save-search dialog.
 */
import { expect, type Locator, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type SearchKind =
  | "projects"
  | "components"
  | "vulnerabilities"
  | "licenses";

export class SearchPageHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────

  /** Open `/search`, optionally pre-seeded with a kind and term. */
  async goto(options: { kind?: SearchKind; q?: string } = {}): Promise<void> {
    const query = new URLSearchParams();
    if (options.kind) query.set("kind", options.kind);
    if (options.q) query.set("q", options.q);
    const suffix = query.toString();
    await this.page.goto(`${this.baseUrl}/search${suffix ? `?${suffix}` : ""}`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("search-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // The summary strip is the honest "settled" signal — it renders whether the
    // result set is empty or not, and only after the query resolves.
    await expect(this.page.getByTestId("search-summary")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("search-loading")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── query + tabs ────────────────────────────────────────────────────

  /** Type a term and wait for the debounced value to reach the URL. */
  async search(term: string): Promise<void> {
    await this.page.getByTestId("search-input").fill(term);
    await expect
      .poll(() => new URL(this.page.url()).searchParams.get("q"), {
        timeout: 5_000,
      })
      .toBe(term.length > 0 ? term : null);
    await this.expectMounted();
  }

  async selectKind(kind: SearchKind): Promise<void> {
    await this.page.getByTestId(`search-tab-${kind}`).click();
    await expect
      .poll(() => new URL(this.page.url()).searchParams.get("kind"), {
        timeout: 5_000,
      })
      .toBe(kind);
    await this.expectMounted();
  }

  /** Which kind the results currently describe, read off the summary strip. */
  async getKind(): Promise<string | null> {
    return this.page.getByTestId("search-summary").getAttribute("data-kind");
  }

  async getTotal(): Promise<number> {
    const raw = await this.page
      .getByTestId("search-summary")
      .getAttribute("data-total");
    return Number.parseInt(raw ?? "0", 10);
  }

  /**
   * Which scans the active tab draws from — `all_scans` or `current_scan`.
   *
   * Read off a `data-` mirror rather than the sentence, so a copy edit or a
   * language switch does not fail the assertion. The value is the contract:
   * components and projects reach through the whole scan history, while
   * vulnerabilities and licences resolve to each project's current scan.
   */
  async getScope(): Promise<string | null> {
    return this.page.getByTestId("search-scope").getAttribute("data-scope");
  }

  rows(): Locator {
    return this.page.getByTestId("search-result-row");
  }

  // ───── facets ──────────────────────────────────────────────────────────

  facet(name: string, value: string): Locator {
    return this.page.getByTestId(`search-facet-${name}-${value}`);
  }

  /** Toggle a facet chip and wait for the narrowed result set to settle. */
  async toggleFacet(name: string, value: string): Promise<void> {
    await this.facet(name, value).click();
    await this.expectMounted();
  }

  async getFacetCount(name: string, value: string): Promise<number> {
    const text = (await this.facet(name, value).textContent()) ?? "";
    const digits = text.match(/\d+/);
    return digits ? Number.parseInt(digits[0], 10) : 0;
  }

  // ───── paging ──────────────────────────────────────────────────────────

  async nextPage(): Promise<void> {
    await this.page.getByTestId("search-page-next").click();
    await this.expectMounted();
  }

  async getPageParam(): Promise<string | null> {
    return new URL(this.page.url()).searchParams.get("page");
  }

  // ───── saving ──────────────────────────────────────────────────────────

  /** Open the dialog, name the search, confirm, and wait for it to close. */
  async saveAs(name: string): Promise<void> {
    await this.page.getByTestId("search-save-trigger").click();
    await expect(this.page.getByTestId("search-save-dialog")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await this.page.getByTestId("search-save-name").fill(name);
    await this.page.getByTestId("search-save-confirm").click();
    await expect(this.page.getByTestId("search-save-dialog")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }
}
