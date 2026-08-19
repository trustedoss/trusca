/**
 * IntakeHarness: `/intake` page domain verbs.
 *
 * The screen only exists where a deployment turned the queue on, so the first
 * verb a spec needs is the one that says which state it is in. Specs that
 * assert the off state are as much the point as the ones that drive the
 * queue: off is what almost every deployment gets.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2):
 *  - No mocking of our own backend. Real HTTP against docker-compose dev.
 *  - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *  - Selectors live inside the harness; spec files never touch CSS/text.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export class IntakeHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  async gotoIntake(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/intake`);
    await expect(this.page.getByTestId("intake-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** The state almost every deployment is in: the queue is not part of it. */
  async expectSurfaceAbsent(): Promise<void> {
    await expect(this.page.getByTestId("intake-disabled")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("intake-ask-form")).toHaveCount(0);
  }

  async expectSurfacePresent(): Promise<void> {
    await expect(this.page.getByTestId("intake-ask-form")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** No sidebar row for a surface this deployment does not have. */
  async expectNoNavEntry(): Promise<void> {
    await expect(this.page.getByTestId("nav-intake")).toHaveCount(0);
  }

  async expectNavEntry(): Promise<void> {
    await expect(this.page.getByTestId("nav-intake").first()).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async ask(
    projectId: string,
    purl: string,
    justification: string,
  ): Promise<void> {
    await this.page.getByTestId("intake-project").fill(projectId);
    await this.page.getByTestId("intake-purl").fill(purl);
    await this.page.getByTestId("intake-justification").fill(justification);
    await this.page.getByTestId("intake-ask").click();
    await expect(this.page.getByTestId("intake-purl")).toHaveValue("");
  }

  async expectRequestFor(purl: string): Promise<void> {
    await expect(
      this.page.getByTestId("intake-list").getByText(purl, { exact: false }),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }
}
