// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/**
 * `GET /v1/dashboard/trends` — how the portfolio's exposure moved over time.
 *
 * Mirrors `schemas/dashboard_trends.py`. The two kinds of number in a point
 * are not interchangeable and the UI must not treat them as such:
 *
 * - `new_findings` / `resolved_findings` are flows — what a scan observed
 *   changing on that day. Zero on a day with no scan because nothing was
 *   measured, not because nothing moved.
 * - `critical_open` / `kev_open` are levels — the standing exposure, carried
 *   forward from the last scan on or before that day.
 *
 * `scan_count` is what separates the two honestly: zero means the levels were
 * inherited rather than re-measured.
 */

/** The windows the backend accepts. Anything else is a 422, by design. */
export const TREND_WINDOWS = [7, 30, 90] as const;
export type TrendWindow = (typeof TREND_WINDOWS)[number];

export interface TrendPoint {
  date: string;
  new_findings: number;
  resolved_findings: number;
  critical_open: number;
  kev_open: number;
  scan_count: number;
}

export interface DashboardTrends {
  period_days: number;
  start_date: string;
  end_date: string;
  points: TrendPoint[];
  totals: { new_findings: number; resolved_findings: number };
  project_count: number;
}

export async function getDashboardTrends(
  days: TrendWindow,
): Promise<DashboardTrends> {
  const { data } = await api.get<DashboardTrends>("/v1/dashboard/trends", {
    params: { days },
  });
  return data;
}

export const TRENDS_QUERY_KEY = ["dashboard", "trends"] as const;

export function useDashboardTrends(days: TrendWindow) {
  return useQuery({
    queryKey: [...TRENDS_QUERY_KEY, days],
    queryFn: () => getDashboardTrends(days),
    // Keyed by window so switching 7 → 30 → 7 is instant on the second visit.
    // History does not change often enough to be worth refetching on focus,
    // and the endpoint reads every open finding in scope.
    staleTime: 5 * 60_000,
    // Switching windows changes the query key, which would otherwise drop
    // back to the pending state: the chart would collapse into skeletons and
    // the page below it would jump. Holding the previous series until the new
    // one lands keeps the layout still — the panel dims instead.
    placeholderData: keepPreviousData,
  });
}
