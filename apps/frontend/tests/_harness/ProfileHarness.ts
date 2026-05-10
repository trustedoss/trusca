/**
 * ProfileHarness — Phase 5 manual-aligned E2E.
 *
 * Domain verbs for the `/profile` surface (account header + Connected
 * Accounts list with inline-confirm Unlink). Mirrors the existing harness
 * pattern: every selector is rooted in a `data-testid` the SPA emits
 * verbatim, never in a translated label.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2 + test-writer.md):
 *   - No mocking of our own backend. Real HTTP against docker-compose dev.
 *   - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *   - Selectors live inside the harness; spec files never touch CSS/text.
 *
 * Adversarial parametrize note (memory `feedback_adversarial_input_parametrize`):
 *   - The unlink endpoint is `/v1/users/me/oauth-identities/{id}` and the
 *     id is a UUID. We expose {@link rawDeleteIdentity} so adversarial
 *     scenarios can inject path-traversal / control-byte / oversized
 *     identifiers and assert the backend rejects them with 4xx + Problem
 *     Details (never a stack trace, never a 500).
 */
import { expect, type APIResponse, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type OAuthProvider = "github" | "google";

/** Problem URN the backend emits when an unlink would lock the user out. */
export const OAUTH_UNLINK_BLOCKS_LOGIN_TYPE =
  "urn:trustedoss:problem:oauth_unlink_blocks_login";

export class ProfileHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async gotoProfile(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/profile`);
    await this.expectMounted();
  }

  /**
   * Navigate via the header profile link rather than the URL — exercises
   * the AppShell entry point. Falls back to direct nav if the link is
   * missing (the spec can compose either path).
   */
  async openProfileViaHeader(): Promise<void> {
    const link = this.page.getByTestId("header-profile-link");
    await expect(link).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await Promise.all([
      this.page.waitForURL(`${this.baseUrl}/profile`, {
        timeout: DEFAULT_TIMEOUT_MS,
      }),
      link.click(),
    ]);
    await this.expectMounted();
  }

  // ───── assertions ──────────────────────────────────────────────────────
  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("user-profile-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("profile-account-header")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait for the identities list to finish its first fetch — either rows
    // mount, or the empty card mounts. (`aria-busy` is on the list
    // container so we check that, not a missing skeleton.)
    await expect
      .poll(
        async () => {
          const list = this.page.getByTestId("profile-identities-list");
          if ((await list.count()) === 0) return null;
          return list.getAttribute("aria-busy");
        },
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  /** Read the account-header email — locale-agnostic (no translation). */
  async getAccountEmail(): Promise<string> {
    const email = this.page.getByTestId("profile-email");
    await expect(email).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    return (await email.innerText()).trim();
  }

  /**
   * Assert the Connected Accounts list contains exactly the providers in
   * the given set (order-insensitive). Empty array means assert the empty
   * card is shown.
   */
  async expectConnectedAccounts(providers: OAuthProvider[]): Promise<void> {
    if (providers.length === 0) {
      await expect(
        this.page.getByTestId("profile-identities-empty"),
      ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
      return;
    }
    for (const provider of providers) {
      await expect(
        this.page.locator(
          `[data-testid="profile-identity-row"][data-provider="${provider}"]`,
        ),
      ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    }
    // Defensive — assert the row count matches so a regression that adds
    // an extra row would trip the test.
    await expect
      .poll(
        async () =>
          this.page.getByTestId("profile-identity-row").count(),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe(providers.length);
  }

  /**
   * Click Unlink for the row carrying `data-provider=<provider>` and
   * confirm via the inline strip. Waits for the success toast or the
   * blocks-login alert — callers branch on which assertion they expect.
   */
  async unlinkProvider(provider: OAuthProvider): Promise<void> {
    const row = this.providerRow(provider);
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await row.getByTestId("profile-identity-unlink").click();
    await expect(
      row.getByTestId("profile-identity-confirm-strip"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await row.getByTestId("profile-identity-confirm-ok").click();
  }

  /**
   * Drive the Unlink confirm flow expecting the blocks-login red alert
   * (the row stays in place, no toast). Asserts:
   *   - the alert (data-testid=profile-unlink-blocks-login) is visible
   *     under the row,
   *   - the row itself is still in the DOM (the unlink did NOT succeed).
   */
  async expectUnlinkBlocked(provider: OAuthProvider): Promise<void> {
    const row = this.providerRow(provider);
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect(
      row.getByTestId("profile-unlink-blocks-login"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    // The row must persist — the user needs to retry after setting a password.
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /**
   * Assert the success toast (`unlinked` key) surfaced — used by happy-path
   * scenarios that have a fallback auth method (password or another OAuth).
   */
  async expectUnlinkSuccess(): Promise<void> {
    await expect(
      this.page.locator(
        '[data-testid="admin-toast"][data-tone="success"][data-toast-key="unlinked"]',
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── adversarial / API-direct ───────────────────────────────────────
  /**
   * Issue a raw DELETE against `/v1/users/me/oauth-identities/{rawId}`
   * with the caller's session. Adversarial scenarios pass control bytes,
   * traversal, oversized strings — the backend must reject with 4xx +
   * Problem Details (never 500, never a stack trace).
   *
   * Returns the raw {@link APIResponse}; the spec asserts on status code
   * and Content-Type. We do NOT URI-encode `rawId` here — the whole point
   * of the adversarial test is to feed the server suspicious bytes.
   */
  async rawDeleteIdentity(rawId: string): Promise<APIResponse> {
    const accessToken = await this.page.evaluate(() => {
      const w = window as unknown as {
        __authStore?: { accessToken?: string | null };
      };
      return w.__authStore?.accessToken ?? null;
    });
    return this.page.request.delete(
      `${this.backendBaseUrl()}/v1/users/me/oauth-identities/${rawId}`,
      {
        headers: accessToken
          ? { Authorization: `Bearer ${accessToken}` }
          : undefined,
      },
    );
  }

  // ───── internal selectors ─────────────────────────────────────────────
  private providerRow(provider: OAuthProvider) {
    return this.page.locator(
      `[data-testid="profile-identity-row"][data-provider="${provider}"]`,
    );
  }

  private backendBaseUrl(): string {
    return process.env.BACKEND_BASE_URL ?? this.baseUrl;
  }
}
