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
import i18n from "@/lib/i18n";

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
    // The signs are in the text, so the direction survives without colour.
    expect(tile.textContent).toContain("+5");
    expect(tile.textContent).toContain("−2");
  });

  it("gives a quiet window no signs at all (B4)", async () => {
    // Nothing found and nothing resolved used to render "+0 −0", which reads
    // as two movements that cancelled rather than as nothing having happened.
    // The signs were static text nodes, unconditional on the value.
    apiGet.mockResolvedValue({
      data: trends({
        points: [point({ date: "2026-07-01", scan_count: 1 })],
        totals: { new_findings: 0, resolved_findings: 0 },
      }),
    });

    renderPanel();

    const tile = await screen.findByTestId("trend-flow-tile");
    expect(tile.textContent).not.toContain("+0");
    expect(tile.textContent).not.toContain("−0");
    // And the words are what now carry the direction the signs used to.
    // Matched without a gap so this catches the labels being removed: they
    // also appear in the legend below the chart, where they sit alone.
    expect(tile.textContent).toContain("New0");
    expect(tile.textContent).toContain("Resolved0");
  });

  it("groups a large count (B4)", async () => {
    // Was a bare toLocaleString, so a browser locale that does not group
    // rendered 12480 with no separator at all.
    apiGet.mockResolvedValue({
      data: trends({
        points: [
          point({ date: "2026-07-01", critical_open: 12480, scan_count: 1 }),
        ],
        totals: { new_findings: 0, resolved_findings: 0 },
      }),
    });

    renderPanel();

    const tile = await screen.findByTestId("trend-critical-tile");
    expect(tile.textContent).toContain("12,480");
  });

  it("asks for the app's language, not the browser's (B4)", async () => {
    // Asserted on the locale handed to Intl rather than on the output: en
    // and ko group the same way, so a wiring regression that dropped the
    // locale entirely would render identically and pass unnoticed.
    const real = Intl.NumberFormat;
    const seen: Array<string | string[] | undefined> = [];
    function Capturing(
      locale?: string | string[],
      options?: Intl.NumberFormatOptions,
    ): Intl.NumberFormat {
      seen.push(locale);
      return new real(locale, options);
    }
    const spy = vi
      .spyOn(Intl, "NumberFormat")
      .mockImplementation(Capturing as unknown as typeof Intl.NumberFormat);

    try {
      apiGet.mockResolvedValue({
        data: trends({
          points: [point({ date: "2026-07-01", critical_open: 5, scan_count: 1 })],
          totals: { new_findings: 0, resolved_findings: 0 },
        }),
      });
      renderPanel();
      await screen.findByTestId("trend-critical-tile");
    } finally {
      spy.mockRestore();
    }

    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((locale) => locale === i18n.resolvedLanguage)).toBe(true);
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
