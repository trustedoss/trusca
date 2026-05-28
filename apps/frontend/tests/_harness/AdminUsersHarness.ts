/**
 * AdminUsersHarness — Phase 4 PR #13.
 *
 * Domain verbs for the ``/admin/users`` surface so e2e specs read like a
 * walk-through instead of CSS soup. Mirrors the {@link PortalPage} pattern:
 * every selector is rooted in a ``data-testid``/``data-*`` attribute the SPA
 * exposes verbatim, never a translated label, so EN / KO renders pass on
 * the same scenarios.
 *
 * Hard rules (CLAUDE.md "품질·보안·운영 표준" §2 + test-writer.md):
 *   - No mocking of our own backend. Real HTTP against docker-compose dev.
 *   - No ``page.waitForTimeout()``. Use Playwright auto-retry assertions.
 *   - Selectors live inside the harness; spec files never touch CSS/text.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type AdminUserRole = "super_admin" | "team_admin" | "developer";
export type AdminUsersRoleFilter = "all" | AdminUserRole;
export type AdminUsersActiveFilter =
  | "all"
  | "active_only"
  | "inactive_only";
export type AdminUserSuccessKey =
  | "role_updated"
  | "deactivated"
  | "activated"
  | "password_reset_sent";
export type AdminUserErrorExtension =
  | "last_super_admin_protected"
  | "cannot_modify_self"
  | "invalid_role_assignment";

export class AdminUsersHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/admin/users`);
    await this.expectMounted();
  }

  /**
   * Asserts the page rendered the AdminLayout chrome plus the users-page
   * body. Fails fast (auto-retry) if the existence-hide guard kicked in.
   */
  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("admin-layout")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-users-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until the table finishes its first fetch so subsequent verbs
    // ("openUserDrawer", "searchByEmail") don't race the loading skeleton.
    await expect
      .poll(
        async () => {
          const busy = await this.page
            .getByTestId("admin-users-table")
            .getAttribute("aria-busy");
          return busy;
        },
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  /**
   * Assert the existence-hide page rendered. Used for non-super-admin
   * actors hitting ``/admin/users``. The route URL should still be
   * ``/admin/users`` — we render in-place rather than redirecting.
   */
  async expectAccessDenied(): Promise<void> {
    await expect(this.page.getByTestId("admin-not-found")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Defensive: assert the admin layout is NOT rendered. Otherwise a
    // mis-wired guard might leak the chrome to non-super-admins.
    await expect(this.page.getByTestId("admin-layout")).toHaveCount(0);
  }

  // ───── filters ─────────────────────────────────────────────────────────
  async searchByEmail(query: string): Promise<void> {
    const input = this.page.getByTestId("admin-users-search");
    await input.fill(query);
    // The toolbar debounces by 300ms before the table refetches. Wait until
    // the table's aria-busy flips back to false rather than a sleep.
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-users-table")
            .getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  async filterByRole(role: AdminUsersRoleFilter): Promise<void> {
    await this.page.getByTestId("admin-users-role-filter").selectOption(role);
    await this.expectMounted();
  }

  async filterByActive(value: AdminUsersActiveFilter): Promise<void> {
    // Toolbar uses the values "all" | "active" | "inactive" — translate the
    // public verb's "active_only" / "inactive_only" to the SPA's tokens so
    // callers don't need to know the implementation detail.
    const wireValue =
      value === "all"
        ? "all"
        : value === "active_only"
          ? "active"
          : "inactive";
    await this.page
      .getByTestId("admin-users-active-filter")
      .selectOption(wireValue);
    await this.expectMounted();
  }

  // ───── row + drawer ────────────────────────────────────────────────────
  async expectUserRow(email: string): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-users-row"][data-email="${cssEscape(email)}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async expectUserRowRole(email: string, role: AdminUserRole): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-users-row"][data-email="${cssEscape(email)}"][data-role="${role}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async openUserDrawer(email: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="admin-users-row"][data-email="${cssEscape(email)}"]`,
    );
    await row.click();
    await expect(this.page.getByTestId("admin-user-drawer")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    // Wait until the drawer's loading skeleton resolves.
    await expect(
      this.page.getByTestId("admin-user-drawer-loading"),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  async closeUserDrawer(): Promise<void> {
    // shadcn Sheet closes via Escape — keeps the harness from depending on
    // the close-button's translated label or its aria-label.
    await this.page.keyboard.press("Escape");
    await expect(this.page.getByTestId("admin-user-drawer")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── mutations from inside the open drawer ───────────────────────────
  async changeRoleTo(
    role: AdminUserRole,
    options?: { teamId?: string },
  ): Promise<void> {
    await this.page
      .getByTestId("admin-user-action-change-role")
      .click();
    await expect(this.page.getByTestId("admin-user-role-form")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await this.page
      .getByTestId("admin-user-role-select")
      .selectOption(role);
    if (role !== "super_admin") {
      // The team_id field appears only for non-super-admin selections.
      const teamInput = this.page.getByTestId("admin-user-team-id");
      await expect(teamInput).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
      await teamInput.fill(options?.teamId ?? "");
    }
    await this.page.getByTestId("admin-user-role-save").click();
  }

  async deactivate(): Promise<void> {
    await this.page.getByTestId("admin-user-action-deactivate").click();
    await expect(this.page.getByTestId("admin-user-confirm-strip")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await this.page.getByTestId("admin-user-confirm-ok").click();
  }

  async activate(): Promise<void> {
    await this.page.getByTestId("admin-user-action-activate").click();
    await expect(this.page.getByTestId("admin-user-confirm-strip")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await this.page.getByTestId("admin-user-confirm-ok").click();
  }

  async resetPassword(): Promise<void> {
    await this.page.getByTestId("admin-user-action-reset").click();
    await expect(this.page.getByTestId("admin-user-confirm-strip")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await this.page.getByTestId("admin-user-confirm-ok").click();
  }

  // ───── toast / error assertions ────────────────────────────────────────
  /**
   * Wait until the most-recent toast carries the given success key. The
   * toast container exposes ``data-toast-key`` verbatim (locale-agnostic).
   */
  async expectSuccessToast(key: AdminUserSuccessKey): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="success"][data-toast-key="${key}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /**
   * Wait until the toast surfaces an error tone with the given Problem
   * extension — the SPA copies the snake_case extension verbatim into
   * ``data-toast-key``. Used for last-super-admin / cannot-modify-self /
   * invalid-role-assignment scenarios.
   */
  async expectErrorAlert(extension: AdminUserErrorExtension): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="error"][data-toast-key="${extension}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }
}

/**
 * Escape a value for inclusion in a CSS attribute selector. Email is unlikely
 * to contain special chars but ``[a-z0-9.@_-]`` is not a strict rule and we
 * want the harness to fail loudly rather than silently mismatch.
 */
function cssEscape(value: string): string {
  return value.replace(/(["\\])/g, "\\$1");
}
