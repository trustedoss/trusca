/**
 * GroupsHarness, group-hierarchy Phase 4 PR 4-A.
 *
 * No screen exists yet for `/v1/groups` (PR 4-B builds it). This harness is
 * written FIRST, per the harness-before-implementation rule, to pin the
 * `data-testid` / `data-*` contract PR 4-B's React components must satisfy.
 * Same selector philosophy as {@link AdminTeamsHarness}: `data-testid` +
 * `data-*` attributes only, never translated text, never a value the API
 * doesn't actually return (a stat's `data-value` must be the literal number
 * the backend sent, not a formatted/rounded string a screenshot test would
 * have to re-parse).
 *
 * Two surfaces, matching the backend's two read shapes:
 *
 *   - List (`GET /v1/groups`): a table with two independent navigations off
 *     each row: `drillInto()` stays on the list and re-queries the row's
 *     children (`?parent_id=`), while `openDetail()` leaves the list for the
 *     group's own detail page. These are DELIBERATELY separate affordances,
 *     not the same click doing double duty. A row that is both "the folder
 *     you open" and "the folder you view" needs two ways to say "open my
 *     children" vs. "tell me about myself".
 *   - Detail (`GET /v1/groups/{id}`, `GET /v1/groups/{id}/members`): the
 *     ancestors breadcrumb (server data, `GroupDetail.ancestors`), the
 *     30-day subtree stats panel, and a Members section that visually
 *     separates direct membership rows from cascade-inherited ones (each
 *     inherited row must show which ancestor it came from).
 *
 * `expectNotFound()` covers the API's existence-hide 404 (a nonexistent
 * group id and an inaccessible-but-real one render identically, see
 * `services.group_directory_service._load_accessible_group`); the harness
 * does not (and must not) expose a way to distinguish the two cases, because
 * the screen it is testing is not supposed to either.
 */
import { expect, type Page } from "@playwright/test";

const DEFAULT_BASE_URL = "http://localhost:5173";
const DEFAULT_TIMEOUT_MS = 10_000;

export class GroupsHarness {
  readonly page: Page;
  readonly baseUrl: string;

  constructor(page: Page, baseUrl: string = DEFAULT_BASE_URL) {
    this.page = page;
    this.baseUrl = baseUrl;
  }

  // ───── list: navigation ────────────────────────────────────────────────

  /** `/groups`: root groups (no `q`, no `parent_id`: GroupsHarness's own drill state starts empty). */
  async goto(): Promise<void> {
    await this.page.goto(`${this.baseUrl}/groups`);
    await this.expectListMounted();
  }

  async expectListMounted(): Promise<void> {
    await expect(this.page.getByTestId("groups-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect
      .poll(
        async () =>
          this.page.getByTestId("groups-table").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  // ───── list: search (q, flat, whole-tree; ignores drill state) ───────

  async search(query: string): Promise<void> {
    const input = this.page.getByTestId("groups-search");
    await input.fill(query);
    await expect
      .poll(
        async () =>
          this.page.getByTestId("groups-table").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  async clearSearch(): Promise<void> {
    await this.search("");
  }

  // ───── list: rows ───────────────────────────────────────────────────────

  private rowLocator(name: string) {
    return this.page.locator(
      `[data-testid="groups-row"][data-group-name="${cssEscape(name)}"]`,
    );
  }

  async expectGroupRow(name: string): Promise<void> {
    await expect(this.rowLocator(name)).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async expectNoGroupRow(name: string): Promise<void> {
    await expect(this.rowLocator(name)).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** Row-level badges: child/project/member counts, all DIRECT (not subtree). */
  async expectRowCounts(
    name: string,
    counts: { childGroups?: number; projects?: number; members?: number },
  ): Promise<void> {
    const row = this.rowLocator(name);
    if (counts.childGroups !== undefined) {
      await expect(row.locator('[data-testid="groups-row-child-count"]')).toHaveAttribute(
        "data-value",
        String(counts.childGroups),
      );
    }
    if (counts.projects !== undefined) {
      await expect(row.locator('[data-testid="groups-row-project-count"]')).toHaveAttribute(
        "data-value",
        String(counts.projects),
      );
    }
    if (counts.members !== undefined) {
      await expect(row.locator('[data-testid="groups-row-member-count"]')).toHaveAttribute(
        "data-value",
        String(counts.members),
      );
    }
  }

  // ───── list: drill-down (row click stays on the list) ──────────────────

  /**
   * Click a row's name to drill into its DIRECT children (`?parent_id=` of
   * that row). Distinct from {@link openDetail}: this never leaves `/groups`.
   */
  async drillInto(name: string): Promise<void> {
    await this.rowLocator(name).getByTestId("groups-row-name").click();
    await expect
      .poll(
        async () =>
          this.page.getByTestId("groups-table").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  /** The inline drill trail above the table, root-first, current group last. */
  async expectDrilldownPath(names: string[]): Promise<void> {
    const segments = this.page.locator(
      '[data-testid="groups-drilldown-breadcrumb-segment"]',
    );
    await expect(segments).toHaveCount(names.length, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
    for (let i = 0; i < names.length; i += 1) {
      await expect(segments.nth(i)).toHaveAttribute("data-group-name", names[i]);
    }
  }

  /** Click one segment of the drill trail to jump back up to that level. */
  async clickDrilldownBreadcrumbSegment(name: string): Promise<void> {
    await this.page
      .locator(
        `[data-testid="groups-drilldown-breadcrumb-segment"][data-group-name="${cssEscape(name)}"]`,
      )
      .click();
    await expect
      .poll(
        async () =>
          this.page.getByTestId("groups-table").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  /** Click the trail's root segment ("all groups") to clear drill state. */
  async clickDrilldownRoot(): Promise<void> {
    await this.page.getByTestId("groups-drilldown-breadcrumb-root").click();
    await expect
      .poll(
        async () =>
          this.page.getByTestId("groups-table").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  // ───── list -> detail ───────────────────────────────────────────────────

  /** Leaves `/groups` for the group's own detail page (`/groups/:groupId`). */
  async openDetail(name: string): Promise<void> {
    await this.rowLocator(name).getByTestId("groups-row-open-detail").click();
    await this.expectDetailMounted();
  }

  // ───── detail: navigation ───────────────────────────────────────────────

  async gotoDetail(groupId: string): Promise<void> {
    await this.page.goto(`${this.baseUrl}/groups/${groupId}`);
  }

  async expectDetailMounted(): Promise<void> {
    await expect(this.page.getByTestId("group-detail-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("group-detail-loading")).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** The API's existence-hide 404: nonexistent and inaccessible render identically. */
  async expectNotFound(): Promise<void> {
    await expect(this.page.getByTestId("group-detail-not-found")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect(this.page.getByTestId("group-detail-page")).toHaveCount(0);
  }

  async expectDetailName(name: string): Promise<void> {
    await expect(this.page.getByTestId("group-detail-name")).toHaveText(name, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  // ───── detail: ancestors breadcrumb (GroupDetail.ancestors, server data) ─

  /** Root-first ancestor chain, NOT including the group itself. */
  async expectBreadcrumb(names: string[]): Promise<void> {
    const segments = this.page.locator(
      '[data-testid="group-detail-breadcrumb-segment"]',
    );
    await expect(segments).toHaveCount(names.length, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
    for (let i = 0; i < names.length; i += 1) {
      await expect(segments.nth(i)).toHaveAttribute("data-group-name", names[i]);
    }
  }

  /** Click one ancestor's breadcrumb segment: navigates to THAT group's own detail page. */
  async clickBreadcrumbSegment(name: string): Promise<void> {
    await this.page
      .locator(
        `[data-testid="group-detail-breadcrumb-segment"][data-group-name="${cssEscape(name)}"]`,
      )
      .click();
    await this.expectDetailMounted();
  }

  // ───── detail: 30-day subtree summary (GroupDetail.stats) ───────────────

  /**
   * Every field is a SUBTREE aggregate (the whole branch under this group,
   * not just this group's own rows), see `schemas.group.GroupSummaryStats`.
   * `data-value` carries the raw integer the API returned, not a formatted
   * string, so a test asserts the number rather than re-parsing a label.
   */
  async expectStats(stats: {
    subtreeScanCount?: number;
    subtreeApprovalsProcessedCount?: number;
    subtreeNewMemberCount?: number;
    windowDays?: number;
  }): Promise<void> {
    if (stats.subtreeScanCount !== undefined) {
      await expect(
        this.page.getByTestId("group-detail-stat-subtree-scan-count"),
      ).toHaveAttribute("data-value", String(stats.subtreeScanCount), {
        timeout: DEFAULT_TIMEOUT_MS,
      });
    }
    if (stats.subtreeApprovalsProcessedCount !== undefined) {
      await expect(
        this.page.getByTestId("group-detail-stat-subtree-approvals-processed"),
      ).toHaveAttribute(
        "data-value",
        String(stats.subtreeApprovalsProcessedCount),
        { timeout: DEFAULT_TIMEOUT_MS },
      );
    }
    if (stats.subtreeNewMemberCount !== undefined) {
      await expect(
        this.page.getByTestId("group-detail-stat-subtree-new-members"),
      ).toHaveAttribute("data-value", String(stats.subtreeNewMemberCount), {
        timeout: DEFAULT_TIMEOUT_MS,
      });
    }
    if (stats.windowDays !== undefined) {
      await expect(this.page.getByTestId("group-detail-stats-panel")).toHaveAttribute(
        "data-window-days",
        String(stats.windowDays),
      );
    }
  }

  // ───── detail: Members section, direct vs. cascade-inherited ──────────

  async openMembersSection(): Promise<void> {
    await this.page.getByTestId("group-detail-tab-members").click();
    await expect(this.page.getByTestId("group-members-panel")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
    await expect
      .poll(
        async () =>
          this.page.getByTestId("group-members-panel").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe("false");
  }

  private directMemberRow(email: string) {
    return this.page.locator(
      `[data-testid="group-members-direct-row"][data-email="${cssEscape(email)}"]`,
    );
  }

  private inheritedMemberRow(email: string) {
    return this.page.locator(
      `[data-testid="group-members-inherited-row"][data-email="${cssEscape(email)}"]`,
    );
  }

  async expectDirectMemberRow(email: string, role?: string): Promise<void> {
    const row = this.directMemberRow(email);
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    if (role !== undefined) {
      await expect(row).toHaveAttribute("data-role", role);
    }
  }

  async expectNoDirectMemberRow(email: string): Promise<void> {
    await expect(this.directMemberRow(email)).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /**
   * An inherited row must carry `data-source-group-name` (which ancestor the
   * membership actually lives on); the whole point of splitting this list
   * from `direct` is that a reader can tell WHERE the access came from, not
   * just that it exists. See `schemas.group.GroupInheritedMemberEntry`.
   */
  async expectInheritedMemberRow(
    email: string,
    sourceGroupName: string,
    role?: string,
  ): Promise<void> {
    const row = this.inheritedMemberRow(email);
    await expect(row).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect(row).toHaveAttribute("data-source-group-name", sourceGroupName);
    if (role !== undefined) {
      await expect(row).toHaveAttribute("data-role", role);
    }
  }

  async expectNoInheritedMemberRow(email: string): Promise<void> {
    await expect(this.inheritedMemberRow(email)).toHaveCount(0, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** The cascade is off (or this group has no ancestors): inherited must be empty. */
  async expectInheritedSectionEmpty(): Promise<void> {
    await expect(
      this.page.getByTestId("group-members-inherited-row"),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
    await expect(
      this.page.getByTestId("group-members-inherited-empty-state"),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }
}

function cssEscape(value: string): string {
  return value.replace(/(["\\])/g, "\\$1");
}
