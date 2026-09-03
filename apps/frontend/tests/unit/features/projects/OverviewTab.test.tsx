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
    eol_count: 0,
    outdated_count: 0,
    malicious_count: 0,
    severity_distribution: { critical: 1, high: 2, medium: 3, low: 6 },
    license_distribution: { forbidden: 1, allowed: 11 },
    risk_score: 80,
    security_score: 80,
    license_score: 30,
    recent_scans: [],
    last_scan_at: null,
    last_succeeded_scan_at: null,
    component_outcome: "components_found" as const,
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
  /** C3 - the scan action the recent-scans empty state offers. */
  onScan?: () => void;
}

function renderTabWithProbe({
  onSelectScan,
  onJumpToComponents,
  onScan,
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
                  onScan={onScan}
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

  it("says an empty scan found nothing, rather than showing a clean page", async () => {
    // Every number here is 0 because the scan produced no components, which is
    // the exact state that used to render as a project with no risk.
    mockedGet.mockResolvedValueOnce(
      overview({
        total_components: 0,
        severity_distribution: {},
        security_score: 0,
        component_outcome: "empty_no_manifests",
      }),
    );
    renderTab();
    const alert = await screen.findByTestId("overview-empty-sbom");
    expect(alert).toHaveAttribute("data-outcome", "empty_no_manifests");
    // The unsupported-ecosystem wording, not the scan-failure wording: the two
    // ask the user for different things.
    expect(alert.textContent).toContain("no dependency manifest we recognise");
  });

  it("separates a failed scan from an unsupported ecosystem", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        total_components: 0,
        severity_distribution: {},
        security_score: 0,
        component_outcome: "empty_with_manifests",
      }),
    );
    renderTab();
    const alert = await screen.findByTestId("overview-empty-sbom");
    expect(alert).toHaveAttribute("data-outcome", "empty_with_manifests");
    expect(alert.textContent).toContain("points at a failure during the scan");
  });

  it("shows NO caveat when the scan found components", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        total_components: 12,
        severity_distribution: {},
        security_score: 0,
        component_outcome: "components_found",
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("overview-empty-sbom"),
    ).not.toBeInTheDocument();
  });

  it("shows NO caveat when the outcome is unknown (null)", async () => {
    mockedGet.mockResolvedValueOnce(
      overview({
        total_components: 12,
        severity_distribution: {},
        security_score: 0,
        component_outcome: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("overview-empty-sbom"),
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

  // ─── W9-#57 — chart-segment toggle (re-click clears the filter) ──────

  it("re-clicking the same severity segment toggles the filter off", async () => {
    // First click sets ?severity=critical, second click clears it. The `tab`
    // param remains because the toggle only acts on the filter facet.
    mockedGet.mockResolvedValue(
      overview({ severity_distribution: { critical: 2, high: 1 } }),
    );
    renderTabWithProbe();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    const seg = screen.getByTestId("severity-bar-critical");
    await userEvent.click(seg);
    await waitFor(() => {
      const search = screen
        .getByTestId("location-probe")
        .getAttribute("data-search");
      expect(search).toContain("severity=critical");
    });
    await userEvent.click(seg);
    await waitFor(() => {
      const search = screen
        .getByTestId("location-probe")
        .getAttribute("data-search");
      expect(search).toContain("tab=vulnerabilities");
      expect(search).not.toContain("severity=");
    });
  });

  it("re-clicking the same license segment toggles the filter off", async () => {
    mockedGet.mockResolvedValue(
      overview({ license_distribution: { forbidden: 1, allowed: 5 } }),
    );
    renderTabWithProbe();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    const seg = screen.getByTestId("license-bar-forbidden");
    await userEvent.click(seg);
    await waitFor(() => {
      const search = screen
        .getByTestId("location-probe")
        .getAttribute("data-search");
      expect(search).toContain("license_category=forbidden");
    });
    await userEvent.click(seg);
    await waitFor(() => {
      const search = screen
        .getByTestId("location-probe")
        .getAttribute("data-search");
      expect(search).toContain("tab=compliance");
      expect(search).toContain("cview=licenses");
      expect(search).not.toContain("license_category=");
    });
  });

  it("clicking a different severity segment replaces the filter", async () => {
    mockedGet.mockResolvedValue(
      overview({ severity_distribution: { critical: 2, high: 3 } }),
    );
    renderTabWithProbe();
    await waitFor(() => {
      expect(screen.getByTestId("overview-tab")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("severity-bar-critical"));
    await userEvent.click(screen.getByTestId("severity-bar-high"));
    await waitFor(() => {
      const search = screen
        .getByTestId("location-probe")
        .getAttribute("data-search");
      expect(search).toContain("severity=high");
      expect(search).not.toContain("severity=critical");
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
            ref: null,
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
            ref: null,
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
            ref: null,
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
    // Neither the Problem's English title nor its English detail reaches the
    // screen: the heading is the surface's own, and the body names the class.
    const text = screen.getByTestId("overview-error").textContent ?? "";
    expect(text).toContain("Could not load overview.");
    expect(text).toContain("You do not have permission to do this.");
    expect(text).not.toContain("Forbidden");
    expect(text).not.toContain("You cannot view this project.");
  });

  it("renders the outdated KPI chip only when outdated_count > 0", async () => {
    mockedGet.mockResolvedValue(overview({ outdated_count: 3 }));
    renderTab();
    const chip = await screen.findByTestId("overview-outdated-chip");
    expect(chip).toHaveAttribute("data-outdated-count", "3");
    expect(chip.textContent).toMatch(/3/);
  });

  it("hides the outdated KPI chip when outdated_count is 0", async () => {
    mockedGet.mockResolvedValue(overview({ outdated_count: 0 }));
    renderTab();
    await screen.findByTestId("overview-severity-card");
    expect(screen.queryByTestId("overview-outdated-chip")).toBeNull();
  });

  it("deep-links the outdated chip to the components tab filtered by outdated", async () => {
    mockedGet.mockResolvedValue(overview({ outdated_count: 2 }));
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <MemoryRouter initialEntries={["/projects/p1"]}>
          <Routes>
            <Route
              path="/projects/:id"
              element={
                <>
                  <OverviewTab projectId="11111111-1111-1111-1111-111111111111" />
                  <LocationProbe />
                </>
              }
            />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const link = await screen.findByTestId("overview-outdated-chip-link");
    await userEvent.click(link);
    await waitFor(() => {
      const search =
        screen.getByTestId("location-probe").getAttribute("data-search") ?? "";
      expect(search).toContain("tab=components");
      expect(search).toContain("outdated=true");
    });
  });

  // -------------------------------------------------------------------
  // C3 - the recent-scans card's copy, and its way forward.
  // -------------------------------------------------------------------

  it("does not promise five scans over an empty table", async () => {
    // "Last five scans for this project" above nothing is a subtitle
    // describing data that is not there. The suppression landed with an
    // earlier wave (G0-5) and was unpinned until now.
    mockedGet.mockResolvedValueOnce(overview());
    renderTabWithProbe();

    await screen.findByTestId("recent-scans-empty");
    const card = screen.getByTestId("overview-recent-scans-card");
    expect(card.textContent).not.toContain("Last five scans");
  });

  it("passes the scan action through to the empty state", async () => {
    const onScan = vi.fn();
    mockedGet.mockResolvedValueOnce(overview());
    renderTabWithProbe({ onScan });

    await screen.findByTestId("recent-scans-empty");
    await userEvent.click(screen.getByTestId("recent-scans-scan"));
    expect(onScan).toHaveBeenCalledOnce();
  });

  it("offers no scan when the reader cannot start one", async () => {
    mockedGet.mockResolvedValueOnce(overview());
    renderTabWithProbe();

    await screen.findByTestId("recent-scans-empty");
    expect(screen.queryByTestId("recent-scans-scan")).toBeNull();
  });
});
