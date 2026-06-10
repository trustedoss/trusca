/**
 * DashboardPage — unit tests (W9-#50 / audit D1-001).
 *
 * We mock the three list endpoints the page fans out to so the tests stay
 * focused on aggregation + rendering behaviour:
 *   - listProjects   → KPI counts + distribution charts + last-scan KPI
 *   - listMyScans    → recent-scans table
 *   - listApprovals  → pending-approvals KPI
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
import type {
  ApprovalListPage,
} from "@/lib/approvalsApi";
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

vi.mock("@/lib/approvalsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/approvalsApi")>(
      "@/lib/approvalsApi",
    );
  return {
    ...actual,
    listApprovals: vi.fn(),
  };
});

// useDemoMode hits /v1/health on mount; stub it so tests don't fan out.
vi.mock("@/hooks/useDemoMode", () => ({
  useDemoMode: () => ({ demoReadOnly: false }),
}));

import { listApprovals } from "@/lib/approvalsApi";
import { listMyScans, listProjects } from "@/lib/projectsApi";

const mockedListProjects = vi.mocked(listProjects);
const mockedListMyScans = vi.mocked(listMyScans);
const mockedListApprovals = vi.mocked(listApprovals);

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
    visibility: "team",
    archived_at: null,
    created_by_user_id: null,
    latest_scan_id: null,
    latest_scan_status: null,
    severity_summary: null,
    license_category_summary: null,
    created_by_user_name: null,
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

function approvalsResponse(total: number): ApprovalListPage {
  return { items: [], total, page: 1, page_size: 1 };
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
    mockedListProjects.mockReset();
    mockedListMyScans.mockReset();
    mockedListApprovals.mockReset();
    // Default empty so individual tests opt-in to richer fixtures.
    mockedListProjects.mockResolvedValue(projectsResponse([]));
    mockedListMyScans.mockResolvedValue(scansResponse([]));
    mockedListApprovals.mockResolvedValue(approvalsResponse(0));
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
    mockedListApprovals.mockResolvedValue(approvalsResponse(7));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
    });
    // All four KPI cards reachable
    expect(screen.getByTestId("dashboard-kpi-projects")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-kpi-vulns")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-kpi-approvals")).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-kpi-last-scan")).toBeInTheDocument();

    // Active projects = 3 (none archived)
    expect(
      screen.getByTestId("dashboard-kpi-projects-value").textContent,
    ).toBe("3");
    // Open vulns = sum across both scanned projects = 14 + 3 = 17
    expect(screen.getByTestId("dashboard-kpi-vulns-value").textContent).toBe(
      "17",
    );
    // Pending approvals total surfaces from approvalsResponse.
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

  it("renders the empty state with a 'Register project' CTA when no projects exist", async () => {
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
    // Simulating super_admin scope: the backend returns projects from
    // multiple teams. We assert the dashboard sums across the slice
    // without filtering client-side.
    mockedListProjects.mockResolvedValue(
      projectsResponse([
        makeProject("Alpha", {
          team_id: "team-a",
          severity_summary: { critical: 1, high: 1, medium: 0, low: 0 },
        }),
        makeProject("Bravo", {
          team_id: "team-b",
          severity_summary: { critical: 0, high: 0, medium: 2, low: 3 },
        }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByTestId("dashboard-kpi-projects-value").textContent,
      ).toBe("2");
    });
    // 1 + 1 + 2 + 3 = 7
    expect(
      screen.getByTestId("dashboard-kpi-vulns-value").textContent,
    ).toBe("7");
  });

  it("KPI 'view all' link navigates to the matching list page", async () => {
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
    mockedListProjects.mockResolvedValue(
      projectsResponse([
        makeProject("Alpha", {
          severity_summary: { critical: 1, high: 0, medium: 0, low: 0 },
        }),
      ]),
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
    // First projects call fails; scans + approvals succeed. The beforeEach
    // default (empty list) serves the retry, so recovery lands on the
    // empty-state branch.
    mockedListProjects.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    const retry = await screen.findByTestId("dashboard-error-retry");
    expect(mockedListProjects).toHaveBeenCalledTimes(1);
    expect(mockedListMyScans).toHaveBeenCalledTimes(1);
    expect(mockedListApprovals).toHaveBeenCalledTimes(1);

    await act(async () => {
      await userEvent.click(retry);
    });

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("dashboard-error")).not.toBeInTheDocument();
    // Only the failed projects query was refetched.
    expect(mockedListProjects).toHaveBeenCalledTimes(2);
    expect(mockedListMyScans).toHaveBeenCalledTimes(1);
    expect(mockedListApprovals).toHaveBeenCalledTimes(1);
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
