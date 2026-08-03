/**
 * AdminTeamsHarness — Phase 4 PR #13.
 *
 * Domain verbs for the ``/admin/teams`` surface. Sibling of
 * {@link AdminUsersHarness} — same selector philosophy (data-testid +
 * data-* attributes only, no translated text).
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type AdminTeamMemberRole = "team_admin" | "developer";
export type AdminTeamSuccessKey =
  | "created"
  | "updated"
  | "deleted"
  | "member_added"
  | "member_removed";
export type AdminTeamErrorExtension =
  | "last_team_admin_protected"
  | "team_has_active_scans"
  | "slug_conflict";

export class AdminTeamsHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── navigation ──────────────────────────────────────────────────────
  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/admin/teams`);
    await this.expectMounted();
  }

  async expectMounted(): Promise<void> {
    await expect(this.page.getByTestId("admin-layout")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-teams-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-teams-table")
            .getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  async expectAccessDenied(): Promise<void> {
    await expect(this.page.getByTestId("admin-not-found")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("admin-layout")).toHaveCount(0);
  }

  // ───── filters ─────────────────────────────────────────────────────────
  async searchByName(query: string): Promise<void> {
    const input = this.page.getByTestId("admin-teams-search");
    await input.fill(query);
    await expect
      .poll(
        async () =>
          this.page
            .getByTestId("admin-teams-table")
            .getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  // ───── row + drawer ────────────────────────────────────────────────────
  async expectTeamRow(name: string): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-teams-row"][data-team-name="${cssEscape(name)}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async openTeamDrawer(name: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="admin-teams-row"][data-team-name="${cssEscape(name)}"]`,
    );
    await row.click();
    await expect(this.page.getByTestId("admin-team-drawer")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(
      this.page.getByTestId("admin-team-drawer-loading"),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  async closeTeamDrawer(): Promise<void> {
    await this.page.keyboard.press("Escape");
    await expect(this.page.getByTestId("admin-team-drawer")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── create flow ─────────────────────────────────────────────────────
  async createTeam(input: {
    name: string;
    slug: string;
    description?: string;
  }): Promise<void> {
    await this.page.getByTestId("admin-teams-new-button").click();
    await expect(
      this.page.getByTestId("admin-teams-create-form"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await this.page.getByTestId("admin-teams-new-name").fill(input.name);
    await this.page.getByTestId("admin-teams-new-slug").fill(input.slug);
    if (input.description !== undefined) {
      await this.page
        .getByTestId("admin-teams-new-description")
        .fill(input.description);
    }
    await this.page.getByTestId("admin-teams-create-save").click();
  }

  // ───── member management (drawer must be open) ─────────────────────────
  async addMember(input: {
    userIdOrEmail: string;
    role: AdminTeamMemberRole;
  }): Promise<void> {
    await this.page.getByTestId("admin-team-action-add-member").click();
    await expect(
      this.page.getByTestId("admin-team-add-member-form"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await this.page
      .getByTestId("admin-team-member-user")
      .fill(input.userIdOrEmail);
    await this.page
      .getByTestId("admin-team-member-role")
      .selectOption(input.role);
    await this.page.getByTestId("admin-team-member-add-save").click();
  }

  async expectMemberRow(email: string): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-team-member-row"][data-email="${cssEscape(email)}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async expectNoMemberRow(email: string): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-team-member-row"][data-email="${cssEscape(email)}"]`,
      ),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  async removeMember(email: string): Promise<void> {
    const row = this.page.locator(
      `[data-testid="admin-team-member-row"][data-email="${cssEscape(email)}"]`,
    );
    await row.locator('[data-testid="admin-team-member-remove"]').click();
    // The remove button toggles into an inline confirm strip on the same
    // row. Wait for the strip to mount, then click "confirm".
    await expect(
      row.locator('[data-testid="admin-team-member-confirm-strip"]'),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await row
      .locator('[data-testid="admin-team-member-confirm-ok"]')
      .click();
  }

  // ───── delete flow ─────────────────────────────────────────────────────
  async deleteTeam(): Promise<void> {
    await this.page.getByTestId("admin-team-action-delete").click();
    await expect(
      this.page.getByTestId("admin-team-delete-confirm"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await this.page.getByTestId("admin-team-delete-confirm-ok").click();
  }

  // ───── toast / error assertions ────────────────────────────────────────
  async expectSuccessToast(key: AdminTeamSuccessKey): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="success"][data-toast-key="${key}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  async expectErrorAlert(extension: AdminTeamErrorExtension): Promise<void> {
    await expect(
      this.page.locator(
        `[data-testid="admin-toast"][data-tone="error"][data-toast-key="${extension}"]`,
      ),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }
}

function cssEscape(value: string): string {
  return value.replace(/(["\\])/g, "\\$1");
}
