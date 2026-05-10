/**
 * ApprovalsHarness — `/approvals` page domain verbs.
 *
 * Sibling of {@link AdminBackupHarness}. Targets the approval-inbox
 * surface where Pending / Under-Review / Approved / Rejected component
 * entries land for super-admin / team-admin review.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2 + test-writer.md):
 *  - No mocking of our own backend. Real HTTP against docker-compose dev.
 *  - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *  - Selectors live inside the harness; spec files never touch CSS/text.
 *
 * Bootstrap scope: this harness covers the verbs the screenshot capture
 * pipeline needs (mount, row enumeration, drawer open). E2E coverage of
 * approve / reject mutations stays the responsibility of a dedicated
 * spec the next time the approvals flow lands an automated suite.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export class ApprovalsHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async gotoApprovals(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/approvals`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("approvals-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // The table + footer wrappers always render (skeleton, rows, or empty
    // cell — all inside the same table). Asserting on those keeps the
    // predicate immune to the empty-tbody zero-height race that the earlier
    // `tbody OR empty-cell` fallback hit during capture runs.
    await expect(this.page.getByTestId("approvals-table")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("approvals-pagination")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until the initial fetch settles so screenshots do not capture
    // the loading skeleton. `aria-busy` flips off once the query resolves.
    await expect
      .poll(
        () =>
          this.page.getByTestId("approvals-table").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .not.toBe("true");
  }

  // ───── list state ─────────────────────────────────────────────────────
  async getRowCount(): Promise<number> {
    return this.page.getByTestId("approvals-row").count();
  }

  async expectEmpty(): Promise<void> {
    await expect(this.page.getByTestId("approvals-empty")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── drawer ─────────────────────────────────────────────────────────
  async openFirstRowDrawer(): Promise<void> {
    const action = this.page.getByTestId("approvals-row-action").first();
    await expect(action).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await action.click();
    await expect(this.page.getByTestId("approvals-drawer")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }
}
