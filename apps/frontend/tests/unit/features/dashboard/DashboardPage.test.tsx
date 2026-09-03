/**
 * DashboardPage — unit tests (W9-#50 / audit D1-001).
 *
 * We mock the endpoints the page reads so the tests stay focused on rendering
 * behaviour:
 *   - getDashboardSummary → every KPI number and both distribution charts
 *   - listProjects        → onboarding checklist + "no projects yet" state
 *   - listMyScans         → recent-scans table
 *
 * The counts used to be derived here from a page of `listProjects`, which is
 * what broke above 100 projects (ER9). They now come from the server, so the
 * fixtures below set them directly instead of implying them.
 *
 * The dashboard renders inside an AppShell-free harness because the chrome
 * (sidebar + header) is exercised separately in App.test.tsx.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardPage } from "@/features/dashboard/DashboardPage";
import { useUIStore } from "@/stores/uiStore";
import type { DashboardSummary } from "@/features/dashboard/api/summary";
import type {
  ProjectListResponse,
  ProjectPublic,
  ScanListResponse,
  ScanPublic,
} from "@/lib/projectsApi";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/projectsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/projectsApi")>(
      "@/lib/projectsApi",
    );
  return {
    ...actual,
    listProjects: vi.fn(),
    listMyScans: vi.fn(),
  };
});

// Mocked at the transport, not at the module: the real `useDashboardSummary`
// calls its own module-local `getDashboardSummary`, which a module mock cannot
// intercept. Same shape as ActionQueuePanel.test.tsx.
const apiGet = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: { get: apiGet } }));

/**
 * Serve `/v1/dashboard/summary` and let every other `api.get` reject.
 *
 * The page also mounts the action-queue and trends panels, which go through
 * the same client. Before this file mocked the transport those requests simply
 * failed and each panel rendered its own error state, which is the condition
 * these tests were written under; answering all of them with the summary
 * payload instead crashes the panels on a shape they never asked for.
 */
function serveSummary(
  summary: DashboardSummary,
  { failFirst = false }: { failFirst?: boolean } = {},
) {
  let summaryCalls = 0;
  apiGet.mockImplementation((url: string) => {
    if (!url.startsWith("/v1/dashboard/summary")) {
      return Promise.reject(new Error(`unstubbed GET ${url}`));
    }
    summaryCalls += 1;
    return failFirst && summaryCalls === 1
      ? Promise.reject(new Error("boom"))
      : Promise.resolve({ data: summary });
  });
}

/** How many times the summary route specifically was requested. */
function summaryCallCount(): number {
  return apiGet.mock.calls.filter((call) =>
    String(call[0]).startsWith("/v1/dashboard/summary"),
  ).length;
}

// useDemoMode hits /v1/health on mount; stub it so tests don't fan out.
vi.mock("@/hooks/useDemoMode", () => ({
  useDemoMode: () => ({ demoReadOnly: false }),
}));

// C2: the two count reads behind the getting-started checklist. Stubbed at
// the hooks rather than left to fail: an unmocked query errors, the checklist
// hides itself on an unknown count, and every assertion about the empty state
// below would then pass because the card was broken rather than because it
// was not wanted.
const policyCount = vi.fn(() => 0);
const apiKeyCount = vi.fn(() => 0);

vi.mock("@/features/policies/useLicensePolicies", () => ({
  useLicensePolicies: () => ({ data: { items: [], total: policyCount() } }),
}));
vi.mock("@/features/integrations/useApiKeys", () => ({
  useApiKeys: () => ({ data: { items: [], total: apiKeyCount() } }),
}));

import { listMyScans, listProjects } from "@/lib/projectsApi";

const mockedListProjects = vi.mocked(listProjects);
const mockedListMyScans = vi.mocked(listMyScans);

// ---------------------------------------------------------------------------
// Fixture builders
// ---------------------------------------------------------------------------

function makeProject(
  name: string,
  overrides: Partial<ProjectPublic> = {},
): ProjectPublic {
  const id =
    overrides.id ??
    `00000000-0000-0000-0000-${name.padEnd(12, "0").slice(0, 12)}`;
  return {
    id,
    team_id: "team-1",
    name,
    slug: name.toLowerCase().replace(/\s+/g, "-"),
    description: null,
    git_url: `https://github.com/example/${name.toLowerCase()}`,
    default_branch: "main",
    declared_license: null,
    ai_usage_context: null,
    business_unit: null,
    owner_contact: null,
    distribution_model: null,
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    latest_scan_status: null,
    severity_summary: null,
    license_category_summary: null,
    created_by_user_name: null,
    team_name: null,
    has_git_credential: false,
    scan_count: 0,
    release_count: 0,
    last_scan_at: null,
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function projectsResponse(items: ProjectPublic[]): ProjectListResponse {
  return { items, total: items.length, page: 1, size: 100 };
}

function makeScan(
  id: string,
  overrides: Partial<ScanPublic> = {},
): ScanPublic {
  return {
    id,
    project_id: "00000000-0000-0000-0000-projAAAAAA",
    kind: "source",
    status: "succeeded",
    progress_percent: 100,
    current_step: null,
    started_at: "2026-05-27T10:00:00Z",
    completed_at: "2026-05-27T10:01:00Z",
    error_message: null,
    requested_by_user_id: null,
    celery_task_id: null,
    metadata: {},
    release: null,
    project_name: "Alpha",
    project_slug: "alpha",
    created_at: "2026-05-27T09:59:00Z",
    updated_at: "2026-05-27T10:01:00Z",
    ...overrides,
  };
}

function scansResponse(items: ScanPublic[]): ScanListResponse {
  return { items, total: items.length, page: 1, size: 10 };
}

/** A zeroed summary, with only the fields a test cares about overridden. */
function summaryResponse(
  overrides: Partial<DashboardSummary> = {},
): DashboardSummary {
  return {
    project_count: 0,
    scan_status_counts: { queued: 0, running: 0, succeeded: 0, failed: 0 },
    vulnerability_severity_counts: {
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      info: 0,
    },
    license_category_counts: {
      prohibited: 0,
      conditional: 0,
      permissive: 0,
      unknown: 0,
    },
    project_severity_counts: {
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      info: 0,
      none: 0,
    },
    project_license_counts: {
      forbidden: 0,
      conditional: 0,
      allowed: 0,
      unknown: 0,
    },
    pending_approvals_count: 0,
    recent_scans: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

function renderPage(initialEntries: string[] = ["/"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DashboardPage", () => {
  beforeEach(() => {
    useUIStore.setState({ onboardingDismissed: false });
    policyCount.mockReturnValue(0);
    apiKeyCount.mockReturnValue(0);
    mockedListProjects.mockReset();
    mockedListMyScans.mockReset();
    apiGet.mockReset();
    // Default empty so individual tests opt-in to richer fixtures.
    mockedListProjects.mockResolvedValue(projectsResponse([]));
    mockedListMyScans.mockResolvedValue(scansResponse([]));
    serveSummary(summaryResponse());
  });
  afterEach(() => {
    void i18n.changeLanguage("en");
  });

  it("renders KPI grid, charts, and recent-scan rows for the loaded portfolio", async () => {
    mockedListProjects.mockResolvedValue(
      projectsResponse([
        makeProject("Alpha", {
          last_scan_at: "2026-05-27T09:00:00Z",
          severity_summary: { critical: 2, high: 5, medium: 3, low: 4 },
          license_category_summary: {
            forbidden: 0,
            conditional: 1,
            allowed: 4,
            unknown: 0,
          },
        }),
        makeProject("Bravo", {
          last_scan_at: "2026-05-26T09:00:00Z",
          severity_summary: { critical: 0, high: 0, medium: 1, low: 2 },
          license_category_summary: {
            forbidden: 1,
            conditional: 0,
            allowed: 3,
            unknown: 0,
          },
        }),
        makeProject("Charlie"), // never scanned
      ]),
    );
    mockedListMyScans.mockResolvedValue(
      scansResponse([
        makeScan("scan-1"),
        makeScan("scan-2", {
          status: "running",
          completed_at: null,
        }),
      ]),
    );
    serveSummary(
      summaryResponse({
        project_count: 3,
        vulnerability_severity_counts: {
          critical: 2,
          high: 5,
          medium: 4,
          low: 6,
          info: 0,
        },
        project_severity_counts: {
          critical: 1,
          high: 0,
          medium: 1,
          low: 0,
          info: 0,
          none: 1,
        },
        pending_approvals_count: 7,
        recent_scans: [
          {
            scan_id: "scan-1",
            project_id: "project-alpha",
            project_name: "Alpha",
            status: "succeeded",
            kind: "source",
            finished_at: "2026-05-27T09:00:00Z",
            release: null,
          },
        ],
      }),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
    });
    // All four KPI cards reachable
    expect(screen.getByTestId("dashboard-kpi-projects")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-kpi-vulns")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-kpi-approvals")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-kpi-last-scan")).toBeInTheDocument();

    // Active projects comes straight from the server aggregate.
    expect(
      screen.getByTestId("dashboard-kpi-projects-value").textContent,
    ).toBe("3");
    // Open vulns = critical + high + medium + low, excluding info.
    expect(screen.getByTestId("dashboard-kpi-vulns-value").textContent).toBe(
      "17",
    );
    // Pending approvals total surfaces from the same aggregate.
    expect(
      screen.getByTestId("dashboard-kpi-approvals-value").textContent,
    ).toBe("7");

    // Charts mount and recent-scans table shows both rows
    expect(
      screen.getByTestId("dashboard-severity-card"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-license-card")).toBeInTheDocument();
    expect(
      await screen.findByTestId("dashboard-recent-scans-table"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByTestId("dashboard-recent-scan-row"),
    ).toHaveLength(2);
  });

  it("leads an empty organisation with the checklist, not a wall of zeros", async () => {
    // C2. The three ways this can go wrong are all visible here: the old
    // empty state saying the same thing twice, the KPI body falling through
    // with every tile at 0, and the checklist not appearing at all.
    mockedListProjects.mockResolvedValue(projectsResponse([]));
    renderPage();

    await screen.findByTestId("onboarding-checklist");
    expect(screen.queryByTestId("dashboard-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-kpi-grid")).not.toBeInTheDocument();
  });

  it("hands the empty state back when the checklist is dismissed", async () => {
    // Dismissing must not leave the dashboard blank: the reader still needs
    // to be told there is nothing here and offered the one action.
    useUIStore.setState({ onboardingDismissed: true });
    mockedListProjects.mockResolvedValue(projectsResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-empty")).toBeInTheDocument();
    });
    const cta = screen.getByTestId("dashboard-empty-cta");
    expect(cta).toHaveAttribute("href", "/projects/new");
    // KPI grid is suppressed when the portfolio is empty so the dashboard
    // reads as "nothing yet" instead of "zeroes everywhere".
    expect(
      screen.queryByTestId("dashboard-kpi-grid"),
    ).not.toBeInTheDocument();
  });

  it("super admin sees the full project slice across teams (mocked)", async () => {
    // Simulating super_admin scope: the aggregate already spans every team the
    // caller can read, and the page renders it without filtering.
    serveSummary(
      summaryResponse({
        project_count: 2,
        vulnerability_severity_counts: {
          critical: 1,
          high: 1,
          medium: 2,
          low: 3,
          info: 0,
        },
      }),
    );
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("dashboard-kpi-projects-value").textContent,
      ).toBe("2");
    });
    // 1 + 1 + 2 + 3 = 7, info excluded
    expect(
      screen.getByTestId("dashboard-kpi-vulns-value").textContent,
    ).toBe("7");
  });

  it("KPI 'view all' link navigates to the matching list page", async () => {
    serveSummary(summaryResponse({ project_count: 1 }));
    mockedListProjects.mockResolvedValue(
      projectsResponse([makeProject("Alpha")]),
    );
    renderPage();
    const link = await screen.findByTestId("dashboard-kpi-projects-view-all");
    expect(link).toHaveAttribute("href", "/projects");
    const approvalsLink = screen.getByTestId(
      "dashboard-kpi-approvals-view-all",
    );
    expect(approvalsLink).toHaveAttribute("href", "/approvals");
  });

  it("chart segment click deep-links into /projects?severity=", async () => {
    serveSummary(
      summaryResponse({
        project_count: 1,
        project_severity_counts: {
          critical: 1,
          high: 0,
          medium: 0,
          low: 0,
          info: 0,
          none: 0,
        },
      }),
    );
    // jsdom's default window.location.assign throws — replace with a spy.
    const assignSpy = vi.fn();
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign: assignSpy },
    });
    try {
      renderPage();
      const seg = await screen.findByTestId("severity-bar-critical");
      await act(async () => {
        await userEvent.click(seg);
      });
      expect(assignSpy).toHaveBeenCalledWith(
        "/projects?severity=critical",
      );
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        value: originalLocation,
      });
    }
  });

  it("renders the same dashboard skeleton under Korean i18n (key parity)", async () => {
    await i18n.changeLanguage("ko");
    useUIStore.setState({ onboardingDismissed: true });
    mockedListProjects.mockResolvedValue(projectsResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
    });
    // The empty-state CTA Korean copy should land — the harness can read it
    // through the `dashboard-empty-cta` testid regardless of language.
    const cta = await screen.findByTestId("dashboard-empty-cta");
    expect(cta.textContent).toContain("프로젝트 등록");
  });

  it("replaces the KPI grid with an inline error state on load failure (M-18)", async () => {
    mockedListProjects.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-error")).toBeInTheDocument();
    });
    // The body is REPLACED — no zero-value KPI tiles, no empty-state CTA.
    expect(
      screen.queryByTestId("dashboard-kpi-grid"),
    ).not.toBeInTheDocument();
    expect(screen.queryByTestId("dashboard-empty")).not.toBeInTheDocument();
    expect(screen.getByTestId("dashboard-error-retry")).toBeInTheDocument();
  });

  it("Retry refetches only the failed queries and restores the dashboard", async () => {
    // First summary call fails; projects + scans succeed. The beforeEach
    // default (empty list) serves the retry, so recovery lands on the
    // empty-state branch - with the checklist dismissed, which is what makes
    // that branch the one recovery lands on.
    useUIStore.setState({ onboardingDismissed: true });
    serveSummary(summaryResponse(), { failFirst: true });
    renderPage();
    const retry = await screen.findByTestId("dashboard-error-retry");
    expect(summaryCallCount()).toBe(1);
    expect(mockedListProjects).toHaveBeenCalledTimes(1);
    expect(mockedListMyScans).toHaveBeenCalledTimes(1);

    await act(async () => {
      await userEvent.click(retry);
    });

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("dashboard-error")).not.toBeInTheDocument();
    // Only the failed summary query was refetched.
    expect(summaryCallCount()).toBe(2);
    expect(mockedListProjects).toHaveBeenCalledTimes(1);
    expect(mockedListMyScans).toHaveBeenCalledTimes(1);
  });

  it("renders the KPI grid normally when all queries succeed (no error state)", async () => {
    mockedListProjects.mockResolvedValue(
      projectsResponse([makeProject("Alpha")]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-kpi-grid")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("dashboard-error")).not.toBeInTheDocument();
  });
});
