/**
 * useProjectsSummary — portfolio-wide aggregates for the project list page.
 *
 * Talks to the same backend endpoint that powered the (now-retired) Dashboard
 * page (``GET /v1/dashboard/summary``). The schema is unchanged; only the
 * hook + the page that renders it moved. Once the Dashboard endpoint is
 * renamed in a follow-up the request URL flips in one place and the hook
 * stays the same.
 *
 * The hook returns the two cards the Projects header surfaces:
 *   - ``vulnerability_severity_counts`` (critical/high/medium/low) — drives the
 *     Severity distribution card and feeds the inline severity filter.
 *   - ``license_category_counts`` (forbidden/conditional/allowed/unknown) —
 *     drives the License classification card (read-only for now; filter
 *     wiring lands once the backend list endpoint accepts a license filter).
 */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export interface ProjectsSummaryResponse {
  project_count: number;
  vulnerability_severity_counts: {
    critical: number;
    high: number;
    medium: number;
    low: number;
  };
  license_category_counts: {
    forbidden: number;
    conditional: number;
    allowed: number;
    unknown: number;
  };
}

export const projectsSummaryKey = ["projects", "summary"] as const;

async function fetchProjectsSummary(): Promise<ProjectsSummaryResponse> {
  const { data } = await api.get<ProjectsSummaryResponse>(
    "/v1/dashboard/summary",
  );
  return data;
}

export function useProjectsSummary() {
  return useQuery({
    queryKey: projectsSummaryKey,
    queryFn: fetchProjectsSummary,
    staleTime: 30_000,
  });
}
