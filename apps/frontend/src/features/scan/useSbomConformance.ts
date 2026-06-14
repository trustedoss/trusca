/**
 * useSbomConformance — feat/model3-conformance-panel.
 *
 * TanStack Query hook for the received-SBOM conformance verdict
 * (`GET /v1/projects/{project_id}/scans/{scan_id}/conformance`). The verdict
 * only exists for `kind: "sbom"` scans, so the caller gates the query with
 * `enabled` (kind === "sbom" && ids present). A 404 means "no verdict yet" or
 * "unreachable project" (existence-hide) — both surface as a quiet
 * `ProblemError` the consumer renders as "no panel", so we disable retries to
 * avoid a pointless re-fetch storm on the expected 404 branch.
 */
import { useQuery } from "@tanstack/react-query";

import {
  getSbomConformance,
  type SbomConformanceRead,
} from "@/lib/projectsApi";

interface UseSbomConformanceOptions {
  /**
   * Only fetch when the scan is actually an SBOM ingest. The caller passes
   * `scan.kind === "sbom"`; combined with the id presence checks below this
   * keeps the query dormant for source/container scans.
   */
  enabled?: boolean;
}

export function useSbomConformance(
  projectId: string | undefined,
  scanId: string | undefined,
  options: UseSbomConformanceOptions = {},
) {
  const hasIds =
    typeof projectId === "string" &&
    projectId.length > 0 &&
    typeof scanId === "string" &&
    scanId.length > 0;
  const enabled = (options.enabled ?? true) && hasIds;

  return useQuery<SbomConformanceRead>({
    queryKey: ["scans", scanId, "sbom-conformance"],
    queryFn: () => getSbomConformance(projectId as string, scanId as string),
    enabled,
    staleTime: 30_000,
    // 404 (no verdict yet / unreachable) is an expected terminal branch, not a
    // transient failure — never retry it.
    retry: false,
  });
}
