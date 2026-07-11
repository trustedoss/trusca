/**
 * useScanScopeFilter — Phase K (PR K-2).
 *
 * Reads the runtime-scope filter telemetry the worker records on
 * `Scan.scan_metadata.scope_filter` ({ applied, dropped, kept }) so the
 * Components summary band can say "excluded N dev/test components". The
 * telemetry rides the ordinary scan detail response (`ScanPublic.metadata`)
 * — no dedicated endpoint.
 *
 * Scan resolution mirrors the tab's own data anchoring: a pinned `?scan=`
 * wins; otherwise the latest *succeeded* scan from the overview's
 * recent-scans list (the same scan the components list renders). Returns
 * `null` while loading, when the scan has no telemetry (filter disabled,
 * pre-Phase-K scan) or when nothing was dropped — callers render nothing in
 * every null case.
 */
import { useQuery } from "@tanstack/react-query";

import type { ScanSummary } from "@/features/projects/api/projectDetailApi";
import { getScan } from "@/lib/projectsApi";

export interface ScopeFilterTelemetry {
  /** Per-ecosystem drop counts, e.g. { maven: 3, npm: 12 }. */
  dropped: Record<string, number>;
  /** Sum of all drops — the headline number. Always > 0 when non-null. */
  totalDropped: number;
}

/**
 * Defensive parse of `scan_metadata.scope_filter`. The blob is
 * worker-written JSONB — treat every level as untyped and return `null`
 * unless there is at least one positive drop count.
 */
export function parseScopeFilter(
  metadata: Record<string, unknown> | undefined,
): ScopeFilterTelemetry | null {
  const raw = metadata?.["scope_filter"];
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const droppedRaw = (raw as Record<string, unknown>)["dropped"];
  if (
    typeof droppedRaw !== "object" ||
    droppedRaw === null ||
    Array.isArray(droppedRaw)
  ) {
    return null;
  }
  const dropped: Record<string, number> = {};
  let total = 0;
  for (const [ecosystem, count] of Object.entries(
    droppedRaw as Record<string, unknown>,
  )) {
    if (typeof count === "number" && Number.isFinite(count) && count > 0) {
      dropped[ecosystem] = count;
      total += count;
    }
  }
  return total > 0 ? { dropped, totalDropped: total } : null;
}

export function useScanScopeFilter(
  scanId: string | undefined,
  recentScans: ScanSummary[] | undefined,
): ScopeFilterTelemetry | null {
  const effectiveScanId =
    scanId ?? recentScans?.find((scan) => scan.status === "succeeded")?.id;
  const query = useQuery({
    queryKey: ["scans", effectiveScanId ?? "", "scope-filter"],
    queryFn: () => getScan(effectiveScanId as string),
    enabled: typeof effectiveScanId === "string" && effectiveScanId.length > 0,
    // Telemetry is immutable once the scan is terminal — no refetch churn.
    staleTime: 5 * 60 * 1000,
  });
  return parseScopeFilter(query.data?.metadata);
}
