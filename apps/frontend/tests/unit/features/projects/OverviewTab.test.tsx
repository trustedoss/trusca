/**
 * OverviewTab — unit tests (PR #10).
 *
 * Mocks the wire layer so we focus on the page's behavior: skeleton loading,
 * RFC 7807 error rendering, and the happy-path assembly of all four panels.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ProjectOverviewResponse } from "@/features/projects/api/projectDetailApi";
import { OverviewTab } from "@/features/projects/components/OverviewTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/projectDetailApi", async () => {
  return {
    getProjectOverview: vi.fn(),
    listProjectComponents: vi.fn(),
    getComponent: vi.fn(),
  };
});

import { getProjectOverview } from "@/features/projects/api/projectDetailApi";

const mockedGet = vi.mocked(getProjectOverview);

function overview(
  overrides: Partial<ProjectOverviewResponse> = {},
): ProjectOverviewResponse {
  return {
    project_id: "11111111-1111-1111-1111-111111111111",
    project_name: "demo",
    total_components: 12,
    severity_distribution: { critical: 1, high: 2, medium: 3, low: 6 },
    license_distribution: { forbidden: 1, allowed: 11 },
    risk_score: 80,
    security_score: 80,
    license_score: 30,
    recent_scans: [],
    last_scan_at: null,
    last_succeeded_scan_at: null,
    current_user_role: "developer",
    has_git_credential: false,
    ...overrides,
  };
}

function renderTab() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <OverviewTab projectId="11111111-1111-1111-1111-111111111111" />
    </QueryClientProvider>,
  );
}

describe("OverviewTab", () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders skeleton while the query is loading", () => {
    mockedGet.mockReturnValue(new Promise(() => {})); // never resolves
    renderTab();
    expect(screen.getByTestId("overview-loading")).toBeInTheDocument();
  });

  it("renders all four panels once data arrives", async () => {
    mockedGet.mockResolvedValueOnce(overview());
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    expect(screen.getByTestId("overview-risk-card")).toBeInTheDocument();
    expect(screen.getByTestId("overview-severity-card")).toBeInTheDocument();
    expect(screen.getByTestId("overview-license-card")).toBeInTheDocument();
    expect(
      screen.getByTestId("overview-recent-scans-card"),
    ).toBeInTheDocument();
    // Risk is now two separate axes (Security / License), not one composite.
    expect(screen.getByTestId("risk-axes")).toBeInTheDocument();
    expect(screen.getByTestId("risk-axis-security")).toHaveAttribute(
      "data-score",
      "80",
    );
    expect(screen.getByTestId("risk-axis-license")).toHaveAttribute(
      "data-score",
      "30",
    );
  });

  it("renders an RFC 7807 problem error", async () => {
    mockedGet.mockRejectedValueOnce(
      new ProblemError("forbidden", {
        status: 403,
        title: "Forbidden",
        detail: "You cannot view this project.",
        problem: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("overview-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("overview-error").textContent).toContain(
      "Forbidden",
    );
    expect(screen.getByTestId("overview-error").textContent).toContain(
      "You cannot view this project.",
    );
  });
});
