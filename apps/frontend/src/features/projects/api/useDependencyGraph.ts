/**
 * useDependencyGraph — BomLens parity Phase H-1 (dependency graph view).
 *
 * Fetches the resolved dependency graph for a project's latest succeeded scan
 * (or a pinned snapshot when `scanId` is supplied). Powers the Cytoscape graph
 * + tree fallback under the Components tab.
 *
 * The backend caps the node set at `node_cap` (default 5000). When the true
 * `node_count` exceeds that cap the response arrives `truncated: true` with
 * EMPTY `nodes`/`edges` — the graph is too large to render, so the UI shows a
 * guidance note and points the user at the table/tree instead.
 *
 * Query key is `["projects", projectId, "dependency-graph", { scanId }]` so it
 * shares the `["projects", projectId]` invalidation prefix — a fresh scan that
 * invalidates the project prefix also drops the cached graph. Pinning a
 * different snapshot refetches independently.
 *
 * Hard rules (CLAUDE.md):
 *   - Server state lives in TanStack Query, never Zustand / useState.
 *   - All 4xx/5xx are problem+json and surface as ProblemError via the shared
 *     `api` interceptor.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { api } from "@/lib/api";

// ---------------------------------------------------------------------------
// Wire types — mirror the backend dependency-graph contract 1:1 (snake_case)
// ---------------------------------------------------------------------------

/**
 * Per-node worst severity. Matches the seven-bucket severity the rest of the
 * project surface uses (see `ComponentSeverity` + the Vulnerabilities
 * `unknown`), so `SeverityBadge` renders it directly.
 */
export type GraphSeverity =
  | "critical"
  | "high"
  | "medium"
  | "low"
  | "info"
  | "none"
  | "unknown";

export interface GraphNode {
  /** Component-version uuid — stable id used by both nodes and edge endpoints. */
  id: string;
  name: string;
  namespace: string | null;
  version: string;
  purl: string;
  /** true when this node is a direct dependency of the project root. */
  direct: boolean;
  /** Distance from the root, or null when the scan carried no depth info. */
  depth: number | null;
  vulnerability_count: number;
  max_severity: GraphSeverity;
}

export interface GraphEdge {
  /** Parent component-version uuid. */
  source: string;
  /** Child component-version uuid. */
  target: string;
}

export interface DependencyGraphResponse {
  scan_id: string;
  /** True total node count — accurate even when truncated. */
  node_count: number;
  /** True total edge count. */
  edge_count: number;
  /** Applied node ceiling (default 5000). */
  node_cap: number;
  /** true when node_count > node_cap; then nodes/edges are empty. */
  truncated: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ---------------------------------------------------------------------------
// Endpoint + hook
// ---------------------------------------------------------------------------

export async function getDependencyGraph(
  projectId: string,
  scanId?: string,
): Promise<DependencyGraphResponse> {
  const { data } = await api.get<DependencyGraphResponse>(
    `/v1/projects/${projectId}/dependency-graph`,
    // Only send scan_id when pinned; omitting it lets the backend resolve the
    // latest succeeded scan.
    { params: scanId ? { scan_id: scanId } : undefined },
  );
  return data;
}

export function dependencyGraphKey(
  projectId: string,
  scanId: string | undefined,
) {
  return [
    "projects",
    projectId,
    "dependency-graph",
    { scanId: scanId ?? null },
  ] as const;
}

export function useDependencyGraph(
  projectId: string | undefined,
  scanId: string | undefined,
  options?: { enabled?: boolean },
): UseQueryResult<DependencyGraphResponse, Error> {
  const enabledByCaller = options?.enabled ?? true;
  const enabled =
    enabledByCaller && typeof projectId === "string" && projectId.length > 0;

  return useQuery({
    queryKey: dependencyGraphKey(projectId ?? "", scanId),
    enabled,
    queryFn: () => getDependencyGraph(projectId as string, scanId),
    // A resolved graph is immutable for a given scan; the 30 s default keeps
    // it fresh enough while letting a tab-switch reuse the cache.
    staleTime: 30_000,
  });
}
