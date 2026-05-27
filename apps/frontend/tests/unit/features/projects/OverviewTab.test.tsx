/**
 * OverviewTab — unit tests (PR #10, W4-B #16 update).
 *
 * Mocks the wire layer so we focus on the page's behavior: skeleton loading,
 * RFC 7807 error rendering, and the happy-path assembly of all panels. W4-B #16
 * removed the Risk Score card and added chart-segment deep-links + status-aware
 * recent-scans row clicks; the suite covers those.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
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
    vuln_data_available: true,
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
      <MemoryRouter initialEntries={["/projects/p1"]}>
        <OverviewTab projectId="11111111-1111-1111-1111-111111111111" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * Render OverviewTab inside a MemoryRouter that exposes the current location
 * via a sentinel element. Tests assert on the resulting `tab=...&...` URL after
 * a chart segment click to confirm the deep-link landed correctly.
 */
function LocationProbe() {
  const location = useLocation();
  return (
    <div
      data-testid="location-probe"
      data-pathname={location.pathname}
      data-search={location.search}
    />
  );
}

interface DeepLinkRenderProps {
  onSelectScan?: (scan: unknown) => void;
  onJumpToComponents?: (scan: unknown) => void;
}

function renderTabWithProbe({
  onSelectScan,
  onJumpToComponents,
}: DeepLinkRenderProps = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/p1"]}>
        <Routes>
          <Route
            path="/projects/p1"
            element={
              <>
                <OverviewTab
                  projectId="11111111-1111-1111-1111-111111111111"
                  onSelectScan={onSelectScan as never}
                  onJumpToComponents={onJumpToComponents as never}
                />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
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

  it("renders the three remaining panels once data arrives — W4-B #16 dropped the Risk card", async () => {
    mockedGet.mockResolvedValueOnce(overview());
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    // Risk Score card removed in W4-B #16. The header's RiskGauge already
    // surfaces the score; the tab now leads with the policy / severity /
    // license panels and a recent-scans list.
    expect(screen.queryByTestId("overview-risk-card")).not.toBeInTheDocument();
    expect(screen.queryByTestId("risk-axes")).not.toBeInTheDocument();
    expect(screen.getByTestId("overview-severity-card")).toBeInTheDocument();
    expect(screen.getByTestId("overview-license-card")).toBeInTheDocument();
    expect(
      screen.getByTestId("overview-recent-scans-card"),
    ).toBeInTheDocument();
  });

  it("warns when the vuln DB was empty at scan time and Security is 0 (#35 Surface B)", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        total_components: 12,
        severity_distribution: {},
        security_score: 0,
        vuln_data_available: false,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(
        screen.getByTestId("overview-vuln-data-unavailable"),
      ).toBeInTheDocument();
    });
  });

  it("shows NO caveat when vuln data was available, even with Security 0", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        total_components: 12,
        severity_distribution: {},
        security_score: 0,
        vuln_data_available: true,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("overview-vuln-data-unavailable"),
    ).not.toBeInTheDocument();
  });

  it("shows NO caveat when availability is unknown (null)", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        total_components: 12,
        severity_distribution: {},
        security_score: 0,
        vuln_data_available: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("overview-vuln-data-unavailable"),
    ).not.toBeInTheDocument();
  });

  // ─── W4-B #16 — chart deep-links + recent-scans status branching ─────

  it("clicking a severity chart segment deep-links to ?tab=vulnerabilities&severity=<key>", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        severity_distribution: { critical: 2, high: 1 },
      }),
    );
    renderTabWithProbe();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    // The chart's bar segment becomes a button when `onSegmentClick` is wired.
    await userEvent.click(screen.getByTestId("severity-bar-critical"));
    await waitFor(() => {
      const search = screen
        .getByTestId("location-probe")
        .getAttribute("data-search");
      expect(search).toContain("tab=vulnerabilities");
      expect(search).toContain("severity=critical");
    });
  });

  it("clicking a license chart segment deep-links to ?tab=compliance&cview=licenses&license_category=<key>", async () => {
    // W4-C #20 — Licenses was absorbed into Compliance. The deeplink now
    // routes to the Compliance tab's Licenses sub-view and carries the
    // category bucket so the inventory still lands filtered.
    mockedGet.mockResolvedValueOnce(
      overview({
        license_distribution: { forbidden: 1, allowed: 5 },
      }),
    );
    renderTabWithProbe();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("license-bar-forbidden"));
    await waitFor(() => {
      const search = screen
        .getByTestId("location-probe")
        .getAttribute("data-search");
      expect(search).toContain("tab=compliance");
      expect(search).toContain("cview=licenses");
      expect(search).toContain("license_category=forbidden");
    });
  });

  it("clicking a succeeded scan row jumps to Components via onJumpToComponents", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        recent_scans: [
          {
            id: "scan-final",
            kind: "source",
            status: "succeeded",
            progress_percent: 100,
            started_at: "2026-05-01T12:00:00Z",
            completed_at: "2026-05-01T12:01:30Z",
            created_at: "2026-05-01T12:00:00Z",
            release: null,
          },
        ],
      }),
    );
    const onSelectScan = vi.fn();
    const onJumpToComponents = vi.fn();
    renderTabWithProbe({ onSelectScan, onJumpToComponents });
    await waitFor(() => {
      expect(screen.getByTestId("recent-scan-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("recent-scan-row"));
    expect(onJumpToComponents).toHaveBeenCalledTimes(1);
    expect(onSelectScan).not.toHaveBeenCalled();
    const scanArg = onJumpToComponents.mock.calls[0]?.[0] as { id: string };
    expect(scanArg.id).toBe("scan-final");
  });

  it("clicking a running scan row re-opens progress via onSelectScan", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        recent_scans: [
          {
            id: "scan-running",
            kind: "source",
            status: "running",
            progress_percent: 42,
            started_at: "2026-05-01T12:00:00Z",
            completed_at: null,
            created_at: "2026-05-01T12:00:00Z",
            release: null,
          },
        ],
      }),
    );
    const onSelectScan = vi.fn();
    const onJumpToComponents = vi.fn();
    renderTabWithProbe({ onSelectScan, onJumpToComponents });
    await waitFor(() => {
      expect(screen.getByTestId("recent-scan-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("recent-scan-row"));
    expect(onSelectScan).toHaveBeenCalledTimes(1);
    expect(onJumpToComponents).not.toHaveBeenCalled();
  });

  it("clicking a failed scan row also jumps to Components (result is final)", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        recent_scans: [
          {
            id: "scan-failed",
            kind: "source",
            status: "failed",
            progress_percent: 100,
            started_at: "2026-05-01T12:00:00Z",
            completed_at: "2026-05-01T12:01:30Z",
            created_at: "2026-05-01T12:00:00Z",
            release: null,
          },
        ],
      }),
    );
    const onSelectScan = vi.fn();
    const onJumpToComponents = vi.fn();
    renderTabWithProbe({ onSelectScan, onJumpToComponents });
    await waitFor(() => {
      expect(screen.getByTestId("recent-scan-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("recent-scan-row"));
    expect(onJumpToComponents).toHaveBeenCalledTimes(1);
    expect(onSelectScan).not.toHaveBeenCalled();
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
