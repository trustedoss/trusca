/**
 * The risk-over-time panel.
 *
 * Two things here are worth more than the rendering assertions.
 *
 * The first is the same distinction the action queue makes: a series that
 * failed to load must not be drawn as a flat line at zero, because a flat
 * line at zero reads as "the portfolio is clean" — the one wrong answer this
 * panel must never give.
 *
 * The second is the levels/flows split. The standing counts are carried
 * forward on days nobody scanned, so the panel says so in words; a chart that
 * shows a flat line without explaining whether it means "nothing changed" or
 * "nobody looked" is asking the reader to guess.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DashboardTrends, TrendPoint } from "@/features/dashboard/api/trends";
import { TrendsPanel } from "@/features/dashboard/TrendsPanel";

// Mocked at the HTTP client, not at the query function: the hook closes over
// its own module's export, so stubbing that export would leave the hook
// calling the original. This way the query key, the params and the error
// path are all still real.
const apiGet = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { get: apiGet } }));

function point(overrides: Partial<TrendPoint> & { date: string }): TrendPoint {
  return {
    new_findings: 0,
    resolved_findings: 0,
    critical_open: 0,
    kev_open: 0,
    scan_count: 0,
    ...overrides,
  };
}

function trends(overrides: Partial<DashboardTrends> = {}): DashboardTrends {
  const points = overrides.points ?? [
    point({ date: "2026-07-01" }),
    point({ date: "2026-07-02" }),
  ];
  return {
    period_days: 30,
    start_date: points[0].date,
    end_date: points[points.length - 1].date,
    points,
    totals: { new_findings: 0, resolved_findings: 0 },
    project_count: 1,
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TrendsPanel />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("TrendsPanel", () => {
  it("defaults to the 30-day window and asks the API for it", async () => {
    apiGet.mockResolvedValue({ data: trends() });

    renderPanel();

    expect(await screen.findByTestId("trends-panel")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith("/v1/dashboard/trends", {
      params: { days: 30 },
    });
    expect(screen.getByTestId("trend-window-30")).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("refetches for the window the user picks", async () => {
    apiGet.mockResolvedValue({ data: trends({ period_days: 7 }) });

    renderPanel();
    await screen.findByTestId("trends-panel");
    await userEvent.click(screen.getByTestId("trend-window-7"));

    expect(apiGet).toHaveBeenLastCalledWith("/v1/dashboard/trends", {
      params: { days: 7 },
    });
  });

  it("holds the previous series while a new window loads", async () => {
    apiGet.mockResolvedValueOnce({
      data: trends({
        points: [point({ date: "2026-07-01", critical_open: 4, scan_count: 1 })],
      }),
    });
    // The second window never resolves during the test, which is the state
    // worth asserting: mid-switch.
    apiGet.mockReturnValueOnce(new Promise(() => {}));

    renderPanel();
    await screen.findByTestId("trends-panel");
    await userEvent.click(screen.getByTestId("trend-window-7"));

    // Still the old numbers, dimmed — not a skeleton, and no layout jump on
    // a page whose content sits below this card.
    const panel = screen.getByTestId("trends-panel");
    expect(panel).toHaveAttribute("data-settling", "true");
    expect(screen.getByTestId("trend-critical-tile")).toHaveAttribute(
      "data-current",
      "4",
    );
    expect(screen.queryByTestId("trends-loading")).toBeNull();
  });

  it("states the span the sparklines cover", async () => {
    apiGet.mockResolvedValue({
      data: trends({
        points: [
          point({ date: "2026-06-30" }),
          point({ date: "2026-07-01" }),
        ],
      }),
    });

    renderPanel();

    // Three sparklines share one time axis and none can carry tick labels at
    // that size, so the span is stated once in text.
    const range = await screen.findByTestId("trends-range");
    expect(range.textContent).toContain("2026-06-30");
    expect(range.textContent).toContain("2026-07-01");
  });

  it("does not draw a clean portfolio when the request failed", async () => {
    apiGet.mockRejectedValue(new Error("boom"));

    renderPanel();

    expect(await screen.findByTestId("trends-error")).toBeInTheDocument();
    // The distinction this test exists for: no chart, no zeros, no all-clear.
    expect(screen.queryByTestId("trends-panel")).toBeNull();
    expect(screen.queryByTestId("trend-spark-critical_open")).toBeNull();
  });

  it("shows the current level and its movement across the window", async () => {
    apiGet.mockResolvedValue({
      data: trends({
        points: [
          point({ date: "2026-07-01", critical_open: 4, kev_open: 2 }),
          point({ date: "2026-07-02", critical_open: 9, kev_open: 1, scan_count: 1 }),
        ],
      }),
    });

    renderPanel();

    const critical = await screen.findByTestId("trend-critical-tile");
    expect(critical).toHaveAttribute("data-current", "9");
    expect(critical).toHaveAttribute("data-delta", "5");
    // Movement is signed text, not only a colour — the direction has to
    // survive for a reader who cannot see the tint.
    expect(critical.textContent).toContain("+5");

    const kev = screen.getByTestId("trend-kev-tile");
    expect(kev).toHaveAttribute("data-delta", "-1");
    expect(kev.textContent).toContain("−1");
  });

  it("says the levels are carried forward when scans are sparse", async () => {
    apiGet.mockResolvedValue({
      data: trends({
        points: [
          point({ date: "2026-07-01", critical_open: 3, scan_count: 1 }),
          point({ date: "2026-07-02", critical_open: 3 }),
        ],
      }),
    });

    renderPanel();

    const footnote = await screen.findByTestId("trends-footnote");
    expect(footnote.textContent).toContain("carry forward");
  });

  it("distinguishes 'nothing scanned' from 'nothing found'", async () => {
    apiGet.mockResolvedValue({
      data: trends({
        project_count: 2,
        points: [point({ date: "2026-07-01" }), point({ date: "2026-07-02" })],
      }),
    });

    renderPanel();

    const footnote = await screen.findByTestId("trends-footnote");
    expect(footnote.textContent).toContain("No successful scan");
  });

  it("renders one bar group per day and totals both directions", async () => {
    apiGet.mockResolvedValue({
      data: trends({
        points: [
          point({ date: "2026-07-01", new_findings: 5, scan_count: 1 }),
          point({ date: "2026-07-02", resolved_findings: 2, scan_count: 1 }),
          point({ date: "2026-07-03" }),
        ],
        totals: { new_findings: 5, resolved_findings: 2 },
      }),
    });

    renderPanel();

    const tile = await screen.findByTestId("trend-flow-tile");
    expect(tile).toHaveAttribute("data-new", "5");
    expect(tile).toHaveAttribute("data-resolved", "2");
    expect(screen.getByTestId("trend-flow-bars").querySelectorAll("g")).toHaveLength(3);
  });

  it("publishes the same numbers as a table", async () => {
    apiGet.mockResolvedValue({
      data: trends({
        points: [
          point({ date: "2026-07-01", new_findings: 1, critical_open: 7, scan_count: 1 }),
        ],
      }),
    });

    renderPanel();

    // A chart nobody can read without seeing it is a claim only sighted
    // users can check; the table is the same data in text.
    const table = await screen.findByTestId("trend-table");
    const cells = [...table.querySelectorAll("tbody td")].map((c) => c.textContent);
    expect(cells).toEqual(["1", "0", "7", "0"]);
  });

  it("labels every chart for a reader who cannot see it", async () => {
    apiGet.mockResolvedValue({ data: trends({ period_days: 7 }) });

    renderPanel();

    await screen.findByTestId("trends-panel");
    for (const chart of screen.getAllByRole("img")) {
      expect(chart.getAttribute("aria-label")).toBeTruthy();
    }
  });
});
