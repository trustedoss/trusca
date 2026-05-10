/**
 * IntegrationsHarness — chore C smoke coverage.
 *
 * Domain verbs for the `/integrations` surface (API keys + webhook copy
 * cards). Mirrors the {@link AuthHarness} pattern: every selector is rooted
 * in a `data-testid` the SPA emits verbatim, never a translated label, so
 * EN / KO renders pass on the same scenarios.
 *
 * Hard rules (CLAUDE.md "품질·보안·운영 표준" §2 + test-writer.md):
 *   - No mocking of our own backend. Real HTTP against docker-compose dev.
 *   - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *   - Selectors live inside the harness; spec files never touch CSS/text.
 *
 * Scope of this harness is intentionally narrow — we cover only the
 * navigation + create-dialog open/close round trip. The actual create +
 * revoke round trip is exercised in unit tests; running it in E2E adds
 * write-flakiness against a live Postgres for negligible additional signal.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export class IntegrationsHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/integrations`);
    await this.expectMounted();
  }

  // ───── assertions ──────────────────────────────────────────────────────
  /**
   * The `integrations-page` testid wraps the whole route surface (header +
   * API keys section + webhooks section). We also assert the Create button
   * to confirm interactive controls hydrated, not just the SSR shell.
   */
  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("integrations-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("integrations-create-key")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── create-dialog round trip ────────────────────────────────────────
  async clickCreate(): Promise<void> {
    await this.page.getByTestId("integrations-create-key").click();
  }

  async expectCreateDialogOpen(): Promise<void> {
    await expect(
      this.page.getByTestId("integrations-create-dialog"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /**
   * Close the create dialog via Escape (the canonical shadcn affordance)
   * and assert the dialog is gone. Using Escape rather than the cancel
   * button keeps the verb decoupled from the button label and double-checks
   * the keyboard a11y path.
   */
  async closeCreateDialog(): Promise<void> {
    await this.page.keyboard.press("Escape");
    await expect(
      this.page.getByTestId("integrations-create-dialog"),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── reveal-once dialog (Phase 5 manual-aligned) ─────────────────────
  /**
   * Assert the one-time reveal dialog mounted with the issued token. The
   * value is rendered via `data-testid=integrations-reveal-key-value` —
   * we check visibility + non-empty content (locale-agnostic; no text
   * comparison). Caller passes the label they used in the create form;
   * the assertion does not bind to it because the dialog title flows
   * through `t()`, but the harness keeps the parameter for spec
   * readability.
   */
  async expectApiKeyOneTimeReveal(_label?: string): Promise<void> {
    const dialog = this.page.getByTestId("integrations-reveal-dialog");
    await expect(dialog).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const value = this.page.getByTestId("integrations-reveal-key-value");
    await expect(value).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const text = (await value.innerText()).trim();
    if (text.length === 0) {
      throw new Error(
        "integrations-reveal-key-value is empty — backend issued no token?",
      );
    }
    // The Copy button must be reachable so the user can move the secret
    // into a vault before dismissing the dialog.
    await expect(
      this.page.getByTestId("integrations-reveal-copy"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── webhooks section (screenshot pipeline) ────────────────────────
  /**
   * Scroll the Webhooks section into the viewport. The section lives below
   * the API keys table on the same `/integrations` page (sections, not
   * tabs). Used by the screenshot capture spec to compose a Webhooks-tab
   * view; not required by interactive E2E.
   */
  async scrollToWebhooks(): Promise<void> {
    const section = this.page.getByTestId("integrations-webhooks-section");
    await expect(section).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await section.scrollIntoViewIfNeeded();
    await expect(this.page.getByTestId("integrations-webhook-github")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("integrations-webhook-gitlab")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** Dismiss the reveal dialog via the Done button. */
  async closeRevealDialog(): Promise<void> {
    await this.page.getByTestId("integrations-reveal-done").click();
    await expect(
      this.page.getByTestId("integrations-reveal-dialog"),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  /**
   * Click Revoke on the row whose `data-key-prefix` matches the supplied
   * prefix, then confirm via the in-dialog destructive button. Waits for
   * the row to leave the table (TanStack invalidation re-fetches the list).
   */
  async revokeKey(prefix: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="integrations-key-row"][data-key-prefix="${cssEscape(prefix)}"]`,
    );
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await row.getByTestId("integrations-key-revoke").click();
    await expect(
      this.page.getByTestId("integrations-revoke-dialog"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await this.page.getByTestId("integrations-revoke-confirm").click();
    await expect(
      this.page.getByTestId("integrations-revoke-dialog"),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
    await expect(row).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }
}

/** Escape a value for inclusion in a CSS attribute selector. */
function cssEscape(value: string): string {
  return value.replace(/(["\\])/g, "\\$1");
}
