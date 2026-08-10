// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useScanProvenance — where a scan's results came from (gap #31).
 *
 * `GET /v1/scans/{scan_id}/provenance`. Served by its own route rather than on
 * the scan body because an inventory can list hundreds of manifests and the
 * scan body is what every list response repeats per row.
 *
 * A scan with nothing recorded answers 200 with two nulls, so an empty result
 * is a normal terminal state rather than an error. The refusal branches (a scan
 * in another team, an id that does not exist) mirror the scan read exactly, and
 * are not retried — the answer will not change on a second ask.
 */
import { useQuery } from "@tanstack/react-query";

import { getScanProvenance, type ScanProvenanceRead } from "@/lib/projectsApi";

interface UseScanProvenanceOptions {
  /**
   * Hold the query until the scan is worth asking about. The caller passes
   * `scan.status === "succeeded"`: a running scan has not written its
   * provenance yet, and asking on every progress frame would spend a request
   * per tick to receive the same two nulls.
   */
  enabled?: boolean;
}

export function useScanProvenance(
  scanId: string | undefined,
  options: UseScanProvenanceOptions = {},
) {
  const hasId = typeof scanId === "string" && scanId.length > 0;
  const enabled = (options.enabled ?? true) && hasId;

  return useQuery<ScanProvenanceRead>({
    queryKey: ["scans", scanId, "provenance"],
    queryFn: () => getScanProvenance(scanId as string),
    enabled,
    // Provenance is written once, at scan time, and never changes afterwards.
    staleTime: 5 * 60_000,
    retry: false,
  });
}
