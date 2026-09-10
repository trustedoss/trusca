/**
 * AdminTeamsHarness, Phase 4 PR #13 + group-hierarchy Phase 5 PR 5-B.
 *
 * Domain verbs for the ``/admin/teams`` surface. Sibling of
 * {@link AdminUsersHarness} — same selector philosophy (data-testid +
 * data-* attributes only, no translated text).
 *
 * ── group-hierarchy testid contract (PR 5-B) ─────────────────────────────
 * The move / create-subgroup verbs below target markup that does not exist
 * yet on ``AdminTeamDrawer`` / ``AdminTeamsPage`` as of this PR, so a
 * frontend-dev agent implements against this contract VERBATIM (see the
 * ``admin-team-actions`` container's existing edit/add-member/delete
 * buttons for the sibling pattern these follow):
 *
 *   - Button ``admin-team-action-move`` inside ``admin-team-actions``
 *     toggles a form ``admin-team-move-form`` containing:
 *       - ``admin-team-move-target``: a native ``<select>``. One option has
 *         ``value=""`` and ``data-testid="admin-team-move-target-option-root"``
 *         (an explicit, always-rendered "move to root" target, NOT a blank/
 *         disabled placeholder; null-parent is a real, distinct candidate).
 *         Every other option's ``value`` is the candidate group's id and it
 *         carries ``data-group-name="<name>"`` (the harness reads this
 *         attribute, never the option's visible/translated text). The
 *         non-root options MUST be filtered to exactly the groups that (a)
 *         are NOT in the open group's own subtree (itself + descendants;
 *         offering one would let the UI propose a cycle the backend only
 *         rejects after a round trip) AND (b) share the open group's
 *         ``organization_id`` (``AdminTeamDetail.organization_id`` /
 *         ``AdminTeamListItem.organization_id``, both added alongside this
 *         PR). Reparent refuses a cross-organization move server-side
 *         regardless, but the picker should not offer a target that always
 *         422s. Both filters apply together, not one or the other.
 *       - ``admin-team-move-save``: submits the reparent. On success emits
 *         the existing ``admin-toast`` success pattern with
 *         ``data-toast-key="moved"`` (see ``AdminTeamSuccessKey``).
 *   - Button ``admin-team-action-add-subgroup`` inside ``admin-team-actions``
 *     toggles a form ``admin-team-subgroup-form`` containing:
 *       - ``admin-team-subgroup-name`` / ``admin-team-subgroup-slug`` /
 *         ``admin-team-subgroup-description`` inputs (mirrors
 *         ``admin-teams-new-*`` on the top-level create form).
 *       - ``admin-team-subgroup-save``: submits the create, emitting
 *         ``data-toast-key="subgroup_created"`` on success.
 *   - ``admin-team-drawer-parent``: an element ALWAYS rendered inside the
 *     open drawer (any visible copy, "Root of organization" for a root
 *     group, "Parent: <name>" otherwise) carrying ``data-parent-name``: the
 *     parent group's name, or ``""`` (present, empty) for a root group.
 *   - Each ``admin-teams-row`` (list page) additionally carries
 *     ``data-parent-group-id``: the parent group's id, or ``""`` (present,
 *     empty) for a root group.
 *   - Error toasts: ``GroupCycleDetected`` → ``data-toast-key="cycle_detected"``,
 *     ``GroupCrossOrganizationNotAllowed`` →
 *     ``data-toast-key="cross_organization_move"`` (both already covered by
 *     the existing ``expectErrorAlert`` verb, see
 *     ``AdminTeamErrorExtension`` below).
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export type AdminTeamMemberRole = "group_admin" | "developer";
export type AdminTeamSuccessKey =
  | "created"
  | "updated"
  | "deleted"
  | "member_added"
  | "member_removed"
  // group-hierarchy Phase 5 PR 5-B
  | "moved"
  | "subgroup_created";
export type AdminTeamErrorExtension =
  | "last_team_admin_protected"
  | "team_has_active_scans"
  | "slug_conflict"
  // group-hierarchy Phase 5 PR 5-B: GroupCycleDetected (409) /
  // GroupCrossOrganizationNotAllowed (422). GroupHierarchyNotFound (404) and
  // GroupSlugConflict (409) carry no Problem extension and fall back to the
  // generic "slug_conflict" / unknown-error toast paths already in place.
  | "cycle_detected"
  | "cross_organization_move";

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

  // ───── group hierarchy (drawer must be open), Phase 5 PR 5-B ─────────
  /**
   * Move the open drawer's group under a new parent. ``newParentName ===
   * null`` moves it to root via the picker's explicit root option, see the
   * class-level testid contract above for the picker's shape.
   */
  async moveGroup(newParentName: string | null): Promise<void> {
    const form = this.page.getByTestId("admin-team-move-form");
    if ((await form.count()) === 0) {
      await this.page.getByTestId("admin-team-action-move").click();
    }
    await expect(form).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });

    const picker = this.page.getByTestId("admin-team-move-target");
    if (newParentName === null) {
      await picker.selectOption({ value: "" });
    } else {
      const option = picker.locator(
        `option[data-group-name="${cssEscape(newParentName)}"]`,
      );
      await expect(option).toHaveCount(1, { timeout: DEFAULT_TIMEOUT_MS });
      const value = await option.getAttribute("value");
      await picker.selectOption(value ?? "");
    }
    await this.page.getByTestId("admin-team-move-save").click();
  }

  /**
   * Opens the move form (if not already open) and asserts the picker's
   * candidate group names, via each option's ``data-group-name`` attribute
   * (never its rendered/translated text), exactly match ``names`` (order-
   * independent). The synthetic root option is not a group name and is
   * excluded from this comparison; use it to pin "own subtree excluded from
   * the picker" by omitting the descendant's name from ``names``.
   */
  async expectMoveTargetOptions(names: string[]): Promise<void> {
    const form = this.page.getByTestId("admin-team-move-form");
    if ((await form.count()) === 0) {
      await this.page.getByTestId("admin-team-action-move").click();
    }
    await expect(form).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });

    const picker = this.page.getByTestId("admin-team-move-target");
    await expect(picker).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    const actual = await picker
      .locator("option[data-group-name]")
      .evaluateAll((opts) =>
        opts.map((o) => o.getAttribute("data-group-name") ?? ""),
      );
    expect(actual.slice().sort()).toEqual([...names].sort());
  }

  /** Creates a subgroup under the open drawer's group. */
  async createSubgroup(input: {
    name: string;
    slug: string;
    description?: string;
  }): Promise<void> {
    await this.page.getByTestId("admin-team-action-add-subgroup").click();
    await expect(
      this.page.getByTestId("admin-team-subgroup-form"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await this.page
      .getByTestId("admin-team-subgroup-name")
      .fill(input.name);
    await this.page
      .getByTestId("admin-team-subgroup-slug")
      .fill(input.slug);
    if (input.description !== undefined) {
      await this.page
        .getByTestId("admin-team-subgroup-description")
        .fill(input.description);
    }
    await this.page.getByTestId("admin-team-subgroup-save").click();
  }

  /**
   * Asserts the open drawer's parent badge. ``null`` asserts the root
   * representation (``data-parent-name=""``).
   */
  async expectParentBadge(parentName: string | null): Promise<void> {
    const badge = this.page.getByTestId("admin-team-drawer-parent");
    await expect(badge).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect(badge).toHaveAttribute(
      "data-parent-name",
      parentName ?? "",
      { timeout: DEFAULT_TIMEOUT_MS },
    );
  }

  /**
   * Asserts the LIST row's ``data-parent-group-id`` attribute. ``null``
   * asserts the root representation (``""``).
   */
  async expectRowParent(
    name: string,
    parentGroupId: string | null,
  ): Promise<void> {
    const row = this.page.locator(
      `[data-testid="admin-teams-row"][data-team-name="${cssEscape(name)}"]`,
    );
    await expect(row).toHaveAttribute(
      "data-parent-group-id",
      parentGroupId ?? "",
      { timeout: DEFAULT_TIMEOUT_MS },
    );
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
