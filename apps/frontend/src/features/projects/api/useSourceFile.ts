/**
 * useSourceFile — G3.3.
 *
 * Fetches one file's bytes (capped) + per-line license matches for the file
 * viewer pane. Keyed by `[projectId, scanId, "source-file", path]` so flipping
 * between tree rows keeps each file cached. Enabled only when a file path is
 * selected.
 *
 * A 404 (no preserved source, or the path is a directory / missing) is not
 * retried and surfaces as the viewer's empty state, not an error toast.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getSourceFile,
  type SourceFileResponse,
} from "@/features/projects/api/sourceTreeApi";
import { retryNon404 } from "@/features/projects/api/useSourceTree";

export function sourceFileKey(
  projectId: string,
  scanId: string | undefined,
  path: string | null,
) {
  return [
    "projects",
    projectId,
    scanId ?? "latest",
    "source-file",
    path ?? "",
  ] as const;
}

export interface UseSourceFileOptions {
  /** Resolved scan id; undefined = latest (server default). */
  scanId?: string;
}

export function useSourceFile(
  projectId: string | undefined,
  path: string | null,
  options: UseSourceFileOptions = {},
): UseQueryResult<SourceFileResponse, Error> {
  const { scanId } = options;
  return useQuery({
    queryKey: sourceFileKey(projectId ?? "", scanId, path),
    enabled:
      typeof projectId === "string" &&
      projectId.length > 0 &&
      typeof path === "string" &&
      path.length > 0,
    queryFn: () =>
      getSourceFile(projectId as string, { path: path as string, scanId }),
    staleTime: 30_000,
    retry: retryNon404,
  });
}
