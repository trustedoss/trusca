/**
 * DashboardPage — unit tests.
 *
 * Mirrors the sibling mocking style (e.g. AdminDiskPage.test.tsx): we mock the
 * thin `dashboardApi` fetch fn so the real TanStack Query hook, render path,
 * and i18n (the test setup loads the actual locale resources) all execute.
 *
 * Coverage targets:
 *   - Severity counts render (Critical/High/… with their numbers).
 *   - Portfolio + scan-status + approvals cards render.
 *   - License distribution segments + legend render.
 *   - Recent-scan rows render and link to /projects/{project_id}.
 *   - Loading skeletons render before the query resolves.
 *   - Empty state (project_count 0) renders the "Register project" CTA.
 *   - Query error surfaces the destructive alert.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardPage } from "@/features/dashboard/DashboardPage";

vi.mock("@/features/dashboard/api/dashboardApi", async () => {
  return {
    getDashboardSummary: vi.fn(),
  };
});

import {
  getDashboardSummary,
  type DashboardSummary,
} from "@/features/dashboard/api/dashboardApi";

const mockedGet = vi.mocked(getDashboardSummary);

function summaryFixture(
  overrides: Partial<DashboardSummary> = {},
): DashboardSummary {
  return {
    project_count: overrides.project_count ?? 4,
    scan_status_counts: overrides.scan_status_counts ?? {
      queued: 1,
      running: 2,
      succeeded: 7,
      failed: 3,
    },
    vulnerability_severity_counts: overrides.vulnerability_severity_counts ?? {
      critical: 5,
      high: 12,
      medium: 30,
      low: 44,
      info: 9,
    },
    license_category_counts: overrides.license_category_counts ?? {
      prohibited: 2,
      conditional: 6,
      permissive: 88,
      unknown: 4,
    },
    pending_approvals_count: overrides.pending_approvals_count ?? 8,
    recent_scans: overrides.recent_scans ?? [
      {
        scan_id: "scan-1",
        project_id: "proj-1",
        project_name: "alpha-service",
        status: "succeeded",
        kind: "source",
        finished_at: "2026-05-25T10:00:00Z",
        release: "v1.2.3",
      },
      {
        scan_id: "scan-2",
        project_id: "proj-2",
        project_name: "beta-image",
        status: "failed",
        kind: "container",
        finished_at: null,
        release: null,
      },
    ],
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <DashboardPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("DashboardPage", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("renders all five severity counts with their numbers", async () => {
    mockedGet.mockResolvedValue(summaryFixture());
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-severity-section")).toBeInTheDocument();
    });

    const cards = screen.getAllByTestId("dashboard-severity-card");
    expect(cards).toHaveLength(5);

    const critical = cards.find(
      (c) => c.getAttribute("data-severity") === "critical",
    )!;
    expect(critical).toHaveAttribute("data-count", "5");
    expect(within(critical).getByTestId("dashboard-severity-count")).toHaveTextContent(
      "5",
    );

    const info = cards.find((c) => c.getAttribute("data-severity") === "info")!;
    expect(info).toHaveAttribute("data-count", "9");
  });

  it("renders portfolio, scan-status, and pending-approvals cards", async () => {
    mockedGet.mockResolvedValue(summaryFixture());
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-project-count")).toBeInTheDocument();
    });

    expect(screen.getByTestId("dashboard-project-count")).toHaveAttribute(
      "data-count",
      "4",
    );
    expect(
      screen.getByTestId("dashboard-scan-status-failed"),
    ).toHaveAttribute("data-count", "3");
    expect(
      screen.getByTestId("dashboard-scan-status-running"),
    ).toHaveAttribute("data-count", "2");

    const approvals = screen.getByTestId("dashboard-approvals-card");
    expect(approvals).toHaveAttribute("data-count", "8");
    expect(approvals).toHaveAttribute("href", "/approvals");
  });

  it("renders the license distribution segments and legend", async () => {
    mockedGet.mockResolvedValue(summaryFixture());
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-license-card")).toBeInTheDocument();
    });

    // 4 non-zero categories → 4 colored segments.
    expect(
      screen.getAllByTestId("dashboard-license-segment"),
    ).toHaveLength(4);

    const prohibited = screen
      .getAllByTestId("dashboard-license-legend")
      .find((l) => l.getAttribute("data-category") === "prohibited")!;
    expect(prohibited).toHaveTextContent("2");
  });

  it("renders recent-scan rows that link to their project detail page", async () => {
    mockedGet.mockResolvedValue(summaryFixture());
    renderPage();

    await waitFor(() => {
      expect(
        screen.getByTestId("dashboard-recent-list"),
      ).toBeInTheDocument();
    });

    const rows = screen.getAllByTestId("dashboard-recent-scan-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveAttribute("href", "/projects/proj-1");
    expect(rows[0]).toHaveTextContent("alpha-service");
    expect(rows[1]).toHaveAttribute("href", "/projects/proj-2");

    // Kind badges are present and labelled.
    const kinds = screen.getAllByTestId("dashboard-recent-scan-kind");
    expect(kinds[0]).toHaveAttribute("data-kind", "source");
    expect(kinds[1]).toHaveAttribute("data-kind", "container");
  });

  it("shows skeletons while the query is loading", () => {
    // Never-resolving promise keeps the query in the loading state.
    mockedGet.mockReturnValue(new Promise<DashboardSummary>(() => {}));
    renderPage();

    expect(screen.getByTestId("dashboard-loading")).toBeInTheDocument();
    expect(
      screen.queryByTestId("dashboard-severity-section"),
    ).not.toBeInTheDocument();
  });

  it("renders the empty state with a register CTA when there are no projects", async () => {
    mockedGet.mockResolvedValue(
      summaryFixture({ project_count: 0, recent_scans: [] }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-empty")).toBeInTheDocument();
    });

    const cta = screen.getByTestId("dashboard-empty-cta");
    expect(cta).toHaveAttribute("href", "/projects/new");
    // The portfolio section should not render in the empty branch.
    expect(
      screen.queryByTestId("dashboard-severity-section"),
    ).not.toBeInTheDocument();
  });

  it("renders the license empty hint and skips zero-count segments", async () => {
    mockedGet.mockResolvedValue(
      summaryFixture({
        // All-zero license counts → the empty hint branch.
        license_category_counts: {
          prohibited: 0,
          conditional: 0,
          permissive: 0,
          unknown: 0,
        },
        // No scans → the recent-empty branch.
        recent_scans: [],
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-license-card")).toBeInTheDocument();
    });
    expect(screen.getByTestId("dashboard-license-empty")).toBeInTheDocument();
    expect(
      screen.queryByTestId("dashboard-license-segment"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("dashboard-recent-empty")).toBeInTheDocument();
  });

  it("renders only the non-zero license segments when one category is empty", async () => {
    mockedGet.mockResolvedValue(
      summaryFixture({
        license_category_counts: {
          prohibited: 0,
          conditional: 3,
          permissive: 10,
          unknown: 0,
        },
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-license-card")).toBeInTheDocument();
    });
    // Only conditional + permissive are non-zero → 2 segments.
    expect(screen.getAllByTestId("dashboard-license-segment")).toHaveLength(2);
  });

  it("surfaces a destructive alert when the query fails", async () => {
    mockedGet.mockRejectedValue(new Error("boom"));
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("dashboard-error")).toBeInTheDocument();
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
