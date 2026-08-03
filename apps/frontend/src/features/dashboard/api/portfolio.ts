// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * `GET /v1/dashboard/portfolio` — every visible project, grouped by team.
 *
 * Mirrors `schemas/dashboard_portfolio.py`. Two fields carry meaning that is
 * easy to lose in rendering:
 *
 * - `scanned` — false means nobody has ever scanned this project. The counts
 *   are zero either way, so without this flag an unscanned project paints
 *   exactly like a clean one.
 * - `truncated` — the grid is capped per team and overall. When it is set the
 *   UI has to say so, or the reader concludes the projects not shown are fine.
 */

export interface PortfolioProject {
  project_id: string;
  project_name: string;
  critical: number;
  high: number;
  medium: number;
  low: number;
  scanned: boolean;
  last_scan_at: string | null;
}

export interface PortfolioTeam {
  team_id: string;
  team_name: string;
  project_count: number;
  projects: PortfolioProject[];
}

export interface DashboardPortfolio {
  teams: PortfolioTeam[];
  team_count: number;
  shown_team_count: number;
  project_count: number;
  shown_project_count: number;
  truncated: boolean;
}

export async function getDashboardPortfolio(): Promise<DashboardPortfolio> {
  const { data } = await api.get<DashboardPortfolio>("/v1/dashboard/portfolio");
  return data;
}

export const PORTFOLIO_QUERY_KEY = ["dashboard", "portfolio"] as const;

export function useDashboardPortfolio() {
  return useQuery({
    queryKey: PORTFOLIO_QUERY_KEY,
    queryFn: getDashboardPortfolio,
    // Composition changes when a project is registered or a scan finishes —
    // neither is frequent enough to poll for, and the aggregate reads every
    // project in scope.
    staleTime: 60_000,
  });
}
