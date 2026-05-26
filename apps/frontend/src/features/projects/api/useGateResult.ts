/**
 * useGateResult — v2.1 UI gap #1.
 *
 * TanStack Query hook for the build-blocking policy-gate verdict shown on the
 * project Overview tab. Query key is `["projects", projectId, "gate-result"]`
 * so it shares the `["projects", projectId]` invalidation prefix with the
 * overview / components queries — re-running a scan refreshes the verdict
 * alongside the rest of the project detail surface.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getGateResult,
  type GateResultResponse,
} from "@/features/projects/api/projectDetailApi";

export function gateResultKey(projectId: string, scanId?: string) {
  return ["projects", projectId, "gate-result", scanId ?? "latest"] as const;
}

export function useGateResult(
  projectId: string | undefined,
  scanId?: string,
): UseQueryResult<GateResultResponse> {
  return useQuery({
    queryKey: gateResultKey(projectId ?? "", scanId),
    queryFn: () => getGateResult(projectId as string, { scanId }),
    enabled: typeof projectId === "string" && projectId.length > 0,
  });
}
