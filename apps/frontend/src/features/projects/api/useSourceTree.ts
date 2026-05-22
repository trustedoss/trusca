/**
 * useSourceTree — G3.3.
 *
 * Lazy per-directory query for the project's preserved-source file tree. Each
 * tree node fires its own query keyed by `[projectId, scanId, "source-tree",
 * path]` so children load on demand as nodes expand — the whole member list is
 * never materialised. Mounting a node calls this hook with `enabled` set once
 * the node is open.
 *
 * 404 (old scan with no preserved source) is NOT retried and surfaces as an
 * empty state in the tab, not an error toast (the tab inspects `error.status`).
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getSourceTree,
  type SourceTreePage,
} from "@/features/projects/api/sourceTreeApi";
import { ProblemError } from "@/lib/problem";

export function sourceTreeKey(
  projectId: string,
  scanId: string | undefined,
  path: string,
) {
  return ["projects", projectId, scanId ?? "latest", "source-tree", path] as const;
}

/**
 * Shared retry predicate for both source-tree queries: a 404 (no preserved
 * source / missing path) is a stable terminal state — retrying just delays the
 * empty state — so it is never retried. Other failures retry up to twice.
 */
export function retryNon404(failureCount: number, error: Error): boolean {
  if (error instanceof ProblemError && error.status === 404) return false;
  return failureCount < 2;
}

export interface UseSourceTreeOptions {
  /** Resolved scan id; undefined = latest (server default). */
  scanId?: string;
  /** Only fetch when true (e.g. the node is expanded). */
  enabled?: boolean;
  /** Page size (max 500). Defaults to the server default when omitted. */
  size?: number;
}

export function useSourceTree(
  projectId: string | undefined,
  path: string,
  options: UseSourceTreeOptions = {},
): UseQueryResult<SourceTreePage, Error> {
  const { scanId, enabled = true, size } = options;
  return useQuery({
    queryKey: sourceTreeKey(projectId ?? "", scanId, path),
    enabled: typeof projectId === "string" && projectId.length > 0 && enabled,
    queryFn: () =>
      getSourceTree(projectId as string, { path, scanId, size }),
    staleTime: 30_000,
    retry: retryNon404,
  });
}
