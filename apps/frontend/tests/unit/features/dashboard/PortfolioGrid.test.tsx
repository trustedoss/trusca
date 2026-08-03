/**
 * The portfolio grid.
 *
 * The assertions that matter are the three ways this view can mislead:
 *
 *   - painting an unscanned project like a clean one (identical numbers, an
 *     opposite meaning);
 *   - showing a capped subset without saying so, which reads as the whole
 *     portfolio;
 *   - rendering an empty grid on a failed request, which reads as "nothing to
 *     worry about".
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  DashboardPortfolio,
  PortfolioProject,
} from "@/features/dashboard/api/portfolio";
import { PortfolioGrid } from "@/features/dashboard/PortfolioGrid";

const apiGet = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({ api: { get: apiGet } }));

function project(overrides: Partial<PortfolioProject> = {}): PortfolioProject {
  return {
    project_id: "p-1",
    project_name: "payments-api",
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    scanned: true,
    last_scan_at: "2026-07-20T10:00:00Z",
    ...overrides,
  };
}

function portfolio(overrides: Partial<DashboardPortfolio> = {}): DashboardPortfolio {
  const teams = overrides.teams ?? [
    {
      team_id: "t-1",
      team_name: "Payments",
      project_count: 1,
      projects: [project()],
    },
  ];
  const shown = teams.reduce((sum, team) => sum + team.projects.length, 0);
  return {
    teams,
    team_count: teams.length,
    shown_team_count: teams.length,
    project_count: shown,
    shown_project_count: shown,
    truncated: false,
    ...overrides,
  };
}

function renderGrid() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PortfolioGrid />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("PortfolioGrid", () => {
  it("groups projects under the team that owns them", async () => {
    apiGet.mockResolvedValue({
      data: portfolio({
        teams: [
          {
            team_id: "t-1",
            team_name: "Payments",
            project_count: 2,
            projects: [
              project({ project_id: "p-1", critical: 3 }),
              project({ project_id: "p-2", project_name: "ledger", high: 1 }),
            ],
          },
        ],
      }),
    });

    renderGrid();

    expect(await screen.findByTestId("portfolio-team-t-1")).toBeInTheDocument();
    expect(screen.getByTestId("portfolio-cell-p-1")).toHaveAttribute(
      "href",
      "/projects/p-1",
    );
    expect(screen.getByTestId("portfolio-cell-p-2")).toHaveAttribute(
      "data-tone",
      "high",
    );
  });

  it("does not paint an unscanned project like a clean one", async () => {
    apiGet.mockResolvedValue({
      data: portfolio({
        teams: [
          {
            team_id: "t-1",
            team_name: "Payments",
            project_count: 2,
            projects: [
              project({ project_id: "p-clean" }),
              project({
                project_id: "p-never",
                project_name: "registered-and-forgotten",
                scanned: false,
                last_scan_at: null,
              }),
            ],
          },
        ],
      }),
    });

    renderGrid();

    const clean = await screen.findByTestId("portfolio-cell-p-clean");
    const never = screen.getByTestId("portfolio-cell-p-never");
    // Same numbers, different state — and the difference is in the text, not
    // only in the tint.
    expect(clean).toHaveAttribute("data-tone", "clean");
    expect(never).toHaveAttribute("data-tone", "unscanned");
    expect(never.textContent).toContain("Never scanned");
    expect(clean.textContent).toContain("Clean");
    // The tooltip is the surface where this distinction is easiest to lose:
    // "Critical 0 · High 0 · …" on an unmeasured project claims it was looked
    // at and found empty.
    expect(never.getAttribute("title")).not.toContain("Critical 0");
    expect(clean.getAttribute("title")).toContain("Critical 0");
  });

  it("tints by the worst bucket and prints that bucket's count", async () => {
    apiGet.mockResolvedValue({
      data: portfolio({
        teams: [
          {
            team_id: "t-1",
            team_name: "Payments",
            project_count: 1,
            projects: [project({ critical: 2, high: 9, medium: 4 })],
          },
        ],
      }),
    });

    renderGrid();

    const cell = await screen.findByTestId("portfolio-cell-p-1");
    expect(cell).toHaveAttribute("data-tone", "critical");
    // Two criticals outrank nine highs: severity is ordinal, not a total.
    expect(cell.textContent).toContain("Critical 2");
  });

  it("says when the grid is showing a subset", async () => {
    apiGet.mockResolvedValue({
      data: portfolio({
        teams: [
          {
            team_id: "t-1",
            team_name: "Payments",
            project_count: 40,
            projects: [project()],
          },
        ],
        team_count: 15,
        shown_team_count: 1,
        project_count: 40,
        shown_project_count: 1,
        truncated: true,
      }),
    });

    renderGrid();

    // Both levels report: the row that was cut, and the grid as a whole.
    const caption = await screen.findByTestId("portfolio-truncated");
    expect(screen.getByTestId("portfolio-team-truncated-t-1")).toBeInTheDocument();
    // A dropped TEAM leaves no row to carry its own caption, so the grid-level
    // one has to name both numbers. Saying "across 15 teams" beside a single
    // rendered row reads as "these projects span all fifteen".
    expect(caption.textContent).toContain("1 of 15 teams");
  });

  it("stays quiet when nothing was cut", async () => {
    apiGet.mockResolvedValue({ data: portfolio() });

    renderGrid();

    await screen.findByTestId("portfolio-grid");
    // A caption that never goes away carries no information.
    expect(screen.queryByTestId("portfolio-truncated")).toBeNull();
    expect(screen.queryByTestId("portfolio-team-truncated-t-1")).toBeNull();
  });

  it("does not render an empty portfolio when the request failed", async () => {
    apiGet.mockRejectedValue(new Error("boom"));

    renderGrid();

    expect(await screen.findByTestId("portfolio-error")).toBeInTheDocument();
    expect(screen.queryByTestId("portfolio-empty")).toBeNull();
    expect(screen.queryByTestId("portfolio-grid")).toBeNull();
  });

  it("offers a first step when there is genuinely nothing", async () => {
    apiGet.mockResolvedValue({
      data: portfolio({
        teams: [],
        team_count: 0,
        project_count: 0,
        shown_project_count: 0,
      }),
    });

    renderGrid();

    expect(await screen.findByTestId("portfolio-empty")).toBeInTheDocument();
  });
});
