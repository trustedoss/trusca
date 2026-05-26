/**
 * TanStack Query hook for the dashboard summary (org/team risk portfolio).
 *
 * Polls every 30s by default so an operator who lands on `/` sees a steady
 * refresh of scan-status / vulnerability counts without a manual click — the
 * same cadence as the admin telemetry pages. The payload is small (a handful
 * of count maps + ≤ 10 recent scans), so the polling cost is negligible.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getDashboardSummary,
  type DashboardSummary,
} from "@/features/dashboard/api/dashboardApi";

export function dashboardSummaryQueryKey() {
  return ["dashboard", "summary"] as const;
}

export function useDashboardSummary(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<DashboardSummary, Error> {
  return useQuery({
    queryKey: dashboardSummaryQueryKey(),
    queryFn: () => getDashboardSummary(),
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
  });
}
