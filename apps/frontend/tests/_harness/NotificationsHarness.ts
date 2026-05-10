/**
 * NotificationsHarness — Phase 5 manual-aligned E2E.
 *
 * Domain verbs for the notification surface — the header bell on the AppShell
 * and the `/notifications` page (Inbox + Preferences sections). Mirrors the
 * {@link AuthHarness} / {@link IntegrationsHarness} pattern: every selector
 * is rooted in a `data-testid` attribute the SPA emits verbatim, never in a
 * translated label, so EN / KO renders pass on the same scenarios.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2 + test-writer.md):
 *   - No mocking of our own backend. Real HTTP against docker-compose dev.
 *   - No `page.waitForTimeout()`. Use Playwright auto-retry assertions.
 *   - Selectors live inside the harness; spec files never touch CSS/text.
 *
 * Manual-walkthrough alignment (Phase 4 fix items):
 *   - Header bell click navigates to `/notifications` (NO dropdown). The
 *     verb {@link openHeaderBell} encodes that contract.
 *   - Preferences require an explicit Save button click — the verb
 *     {@link savePreferences} surfaces it.
 *   - In-app channel cannot be disabled — the verb
 *     {@link expectInAppAlwaysOn} asserts the disabled toggle, and
 *     {@link rawPutPrefs} is provided so adversarial scenarios can hit the
 *     422 guard at the API layer.
 */
import { expect, type APIResponse, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type NotificationChannel = "email" | "slack" | "teams" | "in_app";

export interface PrefsPayload {
  email_enabled: boolean;
  slack_enabled: boolean;
  teams_enabled: boolean;
  in_app_enabled: boolean;
}

export class NotificationsHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async gotoNotifications(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/notifications`);
    await this.expectMounted();
  }

  /**
   * Click the header bell and expect navigation to `/notifications`.
   *
   * Manual walkthrough fix — the bell is the *only* entry point and it
   * must navigate (not open a dropdown). This verb encodes the contract.
   */
  async openHeaderBell(): Promise<void> {
    const bell = this.page.getByTestId("header-bell");
    await expect(bell).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await Promise.all([
      this.page.waitForURL(`${this.baseUrl}/notifications`, {
        timeout: DEFAULT_TIMEOUT_MS,
      }),
      bell.click(),
    ]);
    await this.expectMounted();
  }

  // ───── assertions ──────────────────────────────────────────────────────
  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("notifications-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until either the inbox section list resolves or the empty card
    // mounts so the next verb has a deterministic DOM to query.
    const list = this.page.getByTestId("notifications-list");
    const empty = this.page.getByTestId("notifications-empty");
    await expect(list.or(empty)).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /**
   * Read the bell-badge unread count via `data-unread-count` (locale-
   * agnostic — never the rendered "99+" string). Returns 0 when the badge
   * is hidden because that value is what the backend reports for "no
   * unread".
   */
  async expectUnreadCount(n: number): Promise<void> {
    const bell = this.page.getByTestId("header-bell");
    await expect(bell).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect
      .poll(() => bell.getAttribute("data-unread-count"), {
        timeout: DEFAULT_TIMEOUT_MS,
      })
      .toBe(String(n));
  }

  /**
   * Assert at least one inbox row exists whose visible title contains the
   * given substring. Locale-agnostic on the substring (caller passes a
   * stable seeded title fragment); the row testid is the structural anchor.
   */
  async expectInboxItem(text: string): Promise<void> {
    const row = this.page
      .getByTestId("notifications-row")
      .filter({ hasText: text });
    await expect(row.first()).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /** Expect zero rows (the empty-state card is shown). */
  async expectInboxEmpty(): Promise<void> {
    await expect(this.page.getByTestId("notifications-empty")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── inbox actions ───────────────────────────────────────────────────
  /**
   * Click the row whose `data-notification-id` matches. The page's
   * `handleActivate` issues PATCH /v1/notifications/{id}/read on click;
   * the harness waits until the row's `data-unread` attribute flips to
   * `false` (TanStack Query invalidates the list and re-renders).
   */
  async markAsRead(id: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="notifications-row"][data-notification-id="${id}"]`,
    );
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await row.click();
    // After the mutation succeeds the row re-renders with data-unread="false".
    // If the row carried a `link` the page also navigates; in that case the
    // row may unmount entirely — accept either outcome.
    await expect
      .poll(
        async () => {
          if ((await row.count()) === 0) return "navigated";
          return row.getAttribute("data-unread");
        },
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toMatch(/^(false|navigated)$/);
  }

  /**
   * Click the "Mark all as read" button (only visible when unread > 0).
   * Waits until the toast surfaces.
   */
  async markAllAsRead(): Promise<void> {
    await this.page.getByTestId("notifications-mark-all").click();
    await expect(
      this.page.locator(
        '[data-testid="admin-toast"][data-toast-key="mark_all_done"]',
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /** Pagination — "Previous" button. */
  async clickPrev(): Promise<void> {
    await this.page.getByTestId("notifications-page-prev").click();
    await this.expectMounted();
  }

  /** Pagination — "Next" button. */
  async clickNext(): Promise<void> {
    await this.page.getByTestId("notifications-page-next").click();
    await this.expectMounted();
  }

  /**
   * Assert the pagination controls are visible (i.e. total > PAGE_SIZE).
   * Returns when the controls mount; throws otherwise (auto-retry).
   */
  async expectPaginationVisible(): Promise<void> {
    await expect(
      this.page.getByTestId("notifications-pagination"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  // ───── preferences ─────────────────────────────────────────────────────
  /**
   * The Preferences section lives on the same page below the inbox. This
   * verb scrolls it into view so subsequent toggles are deterministic.
   */
  async gotoPreferences(): Promise<void> {
    const section = this.page.getByTestId("notifications-prefs-section");
    await expect(section).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await section.scrollIntoViewIfNeeded();
    // Wait until the form mounts (skeleton resolved).
    await expect(
      this.page.getByTestId("notifications-prefs-form"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /**
   * Toggle a channel switch. The page tracks dirty state internally; the
   * Save button is disabled until at least one toggle changes value, so
   * tests should call {@link savePreferences} explicitly afterwards.
   *
   * The in-app switch is rendered disabled — attempting to toggle it via
   * this verb throws (the underlying click is a no-op and the assertion
   * fails).
   */
  async togglePreference(
    channel: NotificationChannel,
    enabled: boolean,
  ): Promise<void> {
    const map: Record<NotificationChannel, string> = {
      email: "notifications-prefs-email",
      slack: "notifications-prefs-slack",
      teams: "notifications-prefs-teams",
      in_app: "notifications-prefs-in-app",
    };
    // The Switch component renders the testid on the underlying
    // `<input type="checkbox">`. State surfaces via `aria-checked`
    // ("true" / "false") on the input — `data-state` lives on the
    // wrapping `<label>` and is not addressable through the testid.
    const sw = this.page.getByTestId(map[channel]);
    await expect(sw).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const current = await sw.getAttribute("aria-checked");
    const want = enabled ? "true" : "false";
    if (current === want) return;
    await sw.click();
    await expect
      .poll(() => sw.getAttribute("aria-checked"), {
        timeout: DEFAULT_TIMEOUT_MS,
      })
      .toBe(want);
  }

  /**
   * Click the explicit Save button (manual walkthrough fix — preferences
   * are not auto-saved). Waits for the success toast. Throws via the
   * auto-retry assertion if the button is disabled.
   */
  async savePreferences(): Promise<void> {
    const save = this.page.getByTestId("notifications-prefs-save");
    await expect(save).toBeEnabled({ timeout: DEFAULT_TIMEOUT_MS });
    await save.click();
    await expect(
      this.page.locator(
        '[data-testid="admin-toast"][data-toast-key="prefs_saved"]',
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /**
   * Assert the in-app switch is rendered checked + disabled (PR #36 M3
   * UI guard — the switch surfaces the rule visually; the API guard is
   * exercised by {@link rawPutPrefs}).
   */
  async expectInAppAlwaysOn(): Promise<void> {
    const sw = this.page.getByTestId("notifications-prefs-in-app");
    await expect(sw).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect(sw).toBeDisabled();
    // The Switch renders the testid on the `<input role="switch">`;
    // checked-state surfaces via `aria-checked` on the input itself
    // (`data-state` lives on the wrapping `<label>` and is not reached
    // through the testid). Mirrors `togglePreference`'s contract.
    await expect
      .poll(() => sw.getAttribute("aria-checked"), {
        timeout: DEFAULT_TIMEOUT_MS,
      })
      .toBe("true");
  }

  // ───── adversarial / API-direct ───────────────────────────────────────
  /**
   * Direct PUT against `/v1/users/me/notification-prefs` using the
   * browser's authenticated session (cookies + Authorization header from
   * the SPA's axios layer). Returns the raw {@link APIResponse} so callers
   * can assert on status / Content-Type / problem URN.
   *
   * Used by adversarial scenarios that try to disable in-app delivery —
   * the backend must respond 422 + RFC 7807 with type
   * `urn:trustedoss:problem:notification_in_app_required`.
   *
   * The request reuses the page's storage state by piggy-backing
   * `request.fetch` on the page's context — no token-shuffling needed.
   */
  async rawPutPrefs(payload: PrefsPayload): Promise<APIResponse> {
    // Pull the access token from the in-memory zustand store via the
    // window hook the SPA installs at bootstrap. Cookies (refresh) ride
    // along automatically because the request is issued through the
    // page's BrowserContext.
    const accessToken = await this.page.evaluate(() => {
      const w = window as unknown as {
        __authStore?: { accessToken?: string | null };
      };
      return w.__authStore?.accessToken ?? null;
    });
    return this.page.request.put(
      `${this.backendBaseUrl()}/v1/users/me/notification-prefs`,
      {
        data: payload,
        headers: accessToken
          ? {
              "Content-Type": "application/json",
              Authorization: `Bearer ${accessToken}`,
            }
          : { "Content-Type": "application/json" },
      },
    );
  }

  /**
   * Resolve the backend host. The dev-stack Vite proxy (chore PR
   * `dev-stack-stabilization`) routes `/v1/*` and `/auth/*` from 5173
   * onto the backend service, so by default we issue same-origin
   * requests through `this.baseUrl`. CI / cross-host runs override
   * via env. Order:
   *   1. BACKEND_BASE_URL — explicit override (CI uses this)
   *   2. VITE_API_BASE_URL — mirrors the SPA's runtime resolution
   *      (apps/frontend/src/lib/api.ts) so a single env flips both
   *   3. this.baseUrl — Vite SPA origin, proxied to FastAPI
   */
  private backendBaseUrl(): string {
    return (
      process.env.BACKEND_BASE_URL ??
      process.env.VITE_API_BASE_URL ??
      this.baseUrl
    );
  }
}
