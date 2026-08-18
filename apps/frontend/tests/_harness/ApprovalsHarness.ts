/**
 * ApprovalsHarness — `/approvals` page domain verbs.
 *
 * Sibling of {@link AdminBackupHarness}. Targets the approval-inbox
 * surface where Pending / Under-Review / Approved / Rejected component
 * entries land for super-admin / team-admin review.
 *
 * Hard rules (CLAUDE.md §품질·보안·운영 §2):
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

/** Subset of the BE ApprovalOut the API verbs below surface to specs. */
export interface ApprovalApiRow {
  id: string;
  status: string;
  version: number;
}

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

  /** B2: the drawer is a history entry, so Back closes it and stays put. */
  async expectDrawerInUrl(): Promise<void> {
    await expect(this.page).toHaveURL(/[?&]approval=/, {
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async closeDrawerWithBrowserBack(): Promise<void> {
    await this.page.goBack();
    await expect(this.page.getByTestId("approvals-drawer")).toHaveCount(0);
    await expect(this.page.getByTestId("approvals-page")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  /** B2: arrive at the queue scoped to one project, as the band links. */
  async gotoApprovalsForProject(projectId: string): Promise<void> {
    await this.page.goto(`${this.baseUrl}/approvals?project=${projectId}`);
    await this.expectMounted();
  }

  async expectProjectFilterVisible(): Promise<void> {
    await expect(this.page.getByTestId("approvals-project-filter")).toBeVisible({
      timeout: DEFAULT_TIMEOUT_MS,
    });
  }

  async clearProjectFilter(): Promise<void> {
    await this.page.getByTestId("approvals-project-filter-clear").click();
    await expect(this.page).not.toHaveURL(/[?&]project=/);
  }

  // ───── dispose (mutations) ──────────────────────────────────────────────
  /**
   * Click a drawer action button, then confirm via the inline confirm strip
   * (`approvals-confirm-ok`). The drawer renders the action set by the current
   * status: pending → start-review / reject; under_review → approve / reject.
   */
  private async actAndConfirm(action: string): Promise<void> {
    await this.page.getByTestId(`approvals-action-${action}`).click();
    const ok = this.page.getByTestId("approvals-confirm-ok");
    await expect(ok).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await ok.click();
  }

  /** pending → under_review. */
  async startReview(): Promise<void> {
    await this.actAndConfirm("start-review");
    await this.expectStatus("under_review");
  }

  /** under_review → approved. */
  async approve(): Promise<void> {
    await this.actAndConfirm("approve");
    await this.expectStatus("approved");
  }

  /** Assert the drawer header status badge reflects the given status. */
  async expectStatus(status: string): Promise<void> {
    await expect
      .poll(
        () =>
          this.page
            .getByTestId("approval-status-badge")
            .getAttribute("data-status"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe(status);
  }

  // ───── status filter (PR-6 / M-13) ──────────────────────────────────────
  /**
   * M-13 — assert the inline status filter currently shows `value`. The
   * verified defect: `/approvals` used to land on "all", so disposed
   * (approved / rejected) rows buried the actionable queue; the page now
   * defaults to the compound "open" (= pending + under_review) filter.
   */
  async expectStatusFilterValue(value: string): Promise<void> {
    await expect(this.page.getByTestId("approval-status-filter")).toHaveValue(
      value,
      { timeout: DEFAULT_TIMEOUT_MS },
    );
  }

  /**
   * Pick a status filter option ("open" | "all" | concrete status) and wait
   * for the refetch to settle. URL note: "open" is the default and is kept
   * OUT of the URL; every other value is persisted as `?status=…`.
   */
  async setStatusFilter(value: string): Promise<void> {
    await this.page
      .getByTestId("approval-status-filter")
      .selectOption(value);
    await expect
      .poll(
        () => new URL(this.page.url()).searchParams.get("status"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .toBe(value === "open" ? null : value);
    await expect
      .poll(
        () =>
          this.page.getByTestId("approvals-table").getAttribute("aria-busy"),
        { timeout: DEFAULT_TIMEOUT_MS },
      )
      .not.toBe("true");
  }

  /**
   * Collect each visible row's `data-status`, in DOM order. Locale-agnostic
   * — the attribute carries the wire enum verbatim.
   */
  async getRowStatuses(): Promise<string[]> {
    return this.page
      .getByTestId("approvals-row")
      .evaluateAll((rows) =>
        rows.map((r) => r.getAttribute("data-status") ?? ""),
      );
  }

  // ───── REST verbs (seed-shaping; PR-6 / M-13) ───────────────────────────
  /**
   * POST /auth/login → access token. Used ONCE per spec for the seeded
   * team_admin (transitions to under_review / approved require team_admin).
   * Keep call counts low — the endpoint is rate-limited 5/min/IP unless the
   * dev stack runs with RATELIMIT_DISABLED=1.
   */
  async apiLogin(email: string, password: string): Promise<string> {
    const res = await this.page.request.post(
      `${this.backendBaseUrl()}/auth/login`,
      { data: { email, password } },
    );
    if (!res.ok()) {
      throw new Error(`apiLogin failed: ${res.status()} ${await res.text()}`);
    }
    const body = (await res.json()) as { access_token?: string };
    if (!body.access_token) {
      throw new Error("apiLogin: response carried no access_token");
    }
    return body.access_token;
  }

  /** GET the project's component ids (the approval target axis). */
  async apiListComponentIds(
    token: string,
    projectId: string,
  ): Promise<string[]> {
    const res = await this.page.request.get(
      `${this.backendBaseUrl()}/v1/projects/${projectId}/components`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok()) {
      throw new Error(
        `apiListComponentIds failed: ${res.status()} ${await res.text()}`,
      );
    }
    const body = (await res.json()) as {
      items: Array<{ component_id: string }>;
    };
    return body.items.map((i) => i.component_id);
  }

  /** POST /v1/approvals — open a pending approval for a component. */
  async apiCreateApproval(
    token: string,
    componentId: string,
    projectId: string,
  ): Promise<ApprovalApiRow> {
    const res = await this.page.request.post(
      `${this.backendBaseUrl()}/v1/approvals`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { component_id: componentId, project_id: projectId },
      },
    );
    if (res.status() !== 201) {
      throw new Error(
        `apiCreateApproval failed: ${res.status()} ${await res.text()}`,
      );
    }
    return (await res.json()) as ApprovalApiRow;
  }

  /**
   * PATCH /v1/approvals/{id}/transition with the mandatory If-Match version
   * (optimistic concurrency — the BE 412s on a stale version). Returns the
   * post-commit row so chained transitions can reuse `version`.
   */
  async apiTransitionApproval(
    token: string,
    approval: ApprovalApiRow,
    action: "under_review" | "approved" | "rejected",
  ): Promise<ApprovalApiRow> {
    const res = await this.page.request.patch(
      `${this.backendBaseUrl()}/v1/approvals/${approval.id}/transition`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "If-Match": `"${approval.version}"`,
        },
        data: { action },
      },
    );
    if (!res.ok()) {
      throw new Error(
        `apiTransitionApproval(${action}) failed: ${res.status()} ${await res.text()}`,
      );
    }
    return (await res.json()) as ApprovalApiRow;
  }

  // ───── transition approvals (a status change that needs two people) ────
  //
  // These live here rather than in their own harness because they surface on
  // this page: the queue of status changes waiting on somebody sits above the
  // component queue, and a spec asserting one usually asserts the other.

  /** The panel is absent, not empty, when no request is waiting. */
  async expectNoTransitionApprovals(): Promise<void> {
    await expect(
      this.page.getByTestId("transition-approvals-panel"),
    ).toHaveCount(0);
  }

  async expectTransitionApproval(approvalId: string): Promise<void> {
    await expect(
      this.page.getByTestId(`transition-approval-${approvalId}`),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
  }

  /** The requester sees their own request without buttons: it is not theirs to decide. */
  async expectOwnRequestNotDecidable(approvalId: string): Promise<void> {
    await expect(
      this.page.getByTestId(`transition-approval-own-${approvalId}`),
    ).toBeVisible({ timeout: DEFAULT_TIMEOUT_MS });
    await expect(
      this.page.getByTestId(`transition-approve-${approvalId}`),
    ).toHaveCount(0);
  }

  async approveTransition(approvalId: string): Promise<void> {
    await this.page.getByTestId(`transition-approve-${approvalId}`).click();
    await expect(
      this.page.getByTestId(`transition-approval-${approvalId}`),
    ).toHaveCount(0, { timeout: DEFAULT_TIMEOUT_MS });
  }

  /** POST /v1/transition-approvals: ask for a change one person may not make. */
  async apiRequestTransition(
    token: string,
    findingId: string,
    targetStatus: string,
    justification: string,
  ): Promise<{ id: string; state: string }> {
    const res = await this.page.request.post(
      `${this.backendBaseUrl()}/v1/transition-approvals`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: {
          finding_id: findingId,
          target_status: targetStatus,
          justification,
        },
      },
    );
    if (res.status() !== 201) {
      throw new Error(
        `apiRequestTransition failed: ${res.status()} ${await res.text()}`,
      );
    }
    return (await res.json()) as { id: string; state: string };
  }

  /**
   * PUT the team's policy so a status starts needing a second person.
   *
   * The team scope rather than the organization's: the PUT replaces the row,
   * so a spec writing here cannot disturb settings another spec left on a
   * shared organization default.
   */
  async apiSetApprovalRequiredStatuses(
    token: string,
    teamId: string,
    statuses: string[],
  ): Promise<void> {
    const res = await this.page.request.put(
      `${this.backendBaseUrl()}/v1/gate-policies/teams/${teamId}`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { approval_required_statuses: statuses },
      },
    );
    if (!res.ok()) {
      throw new Error(
        `apiSetApprovalRequiredStatuses failed: ${res.status()} ${await res.text()}`,
      );
    }
  }

  /** The first finding on a project, as the target of a request. */
  async apiFirstFindingId(token: string, projectId: string): Promise<string> {
    const res = await this.page.request.get(
      `${this.backendBaseUrl()}/v1/projects/${projectId}/vulnerabilities`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok()) {
      throw new Error(
        `apiFirstFindingId failed: ${res.status()} ${await res.text()}`,
      );
    }
    const body = (await res.json()) as { items: Array<{ id: string }> };
    if (body.items.length === 0) {
      throw new Error("apiFirstFindingId: the seeded project has no findings");
    }
    return body.items[0].id;
  }

  /** PATCH the status, returning code and body so a refusal can be asserted. */
  async apiTransitionFindingRaw(
    token: string,
    findingId: string,
    targetStatus: string,
    justification: string,
  ): Promise<{ status: number; body: Record<string, unknown> }> {
    const res = await this.page.request.patch(
      `${this.backendBaseUrl()}/v1/vulnerability_findings/${findingId}/status`,
      {
        headers: { Authorization: `Bearer ${token}` },
        data: { status: targetStatus, justification },
      },
    );
    return {
      status: res.status(),
      body: (await res.json()) as Record<string, unknown>,
    };
  }

  async apiTransitionFinding(
    token: string,
    findingId: string,
    targetStatus: string,
    justification: string,
  ): Promise<void> {
    const result = await this.apiTransitionFindingRaw(
      token,
      findingId,
      targetStatus,
      justification,
    );
    if (result.status !== 200) {
      throw new Error(
        `apiTransitionFinding(${targetStatus}) failed: ${result.status} ` +
          `${JSON.stringify(result.body)}`,
      );
    }
  }

  async apiFindingStatus(token: string, findingId: string): Promise<string> {
    const res = await this.page.request.get(
      `${this.backendBaseUrl()}/v1/vulnerability_findings/${findingId}`,
      { headers: { Authorization: `Bearer ${token}` } },
    );
    if (!res.ok()) {
      throw new Error(
        `apiFindingStatus failed: ${res.status()} ${await res.text()}`,
      );
    }
    return ((await res.json()) as { status: string }).status;
  }

  /**
   * Same resolution order as NotificationsHarness.backendBaseUrl — default
   * is same-origin through the Vite proxy (`/v1`, `/auth`), override via
   * BACKEND_BASE_URL / VITE_API_BASE_URL for cross-host runs.
   */
  private backendBaseUrl(): string {
    return (
      process.env.BACKEND_BASE_URL ??
      process.env.VITE_API_BASE_URL ??
      this.baseUrl
    );
  }
}
