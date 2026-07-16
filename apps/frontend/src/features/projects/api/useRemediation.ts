/**
 * useRemediation — TanStack Query hooks for the project Remediation tab.
 *
 * Server state lives exclusively in TanStack Query (no Zustand). Keys follow
 * the tuple pattern in CLAUDE.md:
 *   ["remediation", projectId, "pull-requests", { page, page_size }]
 *
 * The dry-run is an on-demand *mutation* (it is a `POST` that computes a fresh
 * preview each click; caching it as a query would surface a stale edit), while
 * the PR-create mutation invalidates the PR-list prefix so the freshly opened
 * PR appears without a manual refresh.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  createNpmPullRequest,
  listRemediationPullRequests,
  npmDryRun,
  type ListRemediationPullRequestsParams,
  type NpmDryRunResponse,
  type RemediationManifestBody,
  type RemediationPullRequest,
  type RemediationPullRequestListPage,
} from "@/lib/remediationApi";
import { ProblemError } from "@/lib/problem";

// ---------------------------------------------------------------------------
// Query keys
// ---------------------------------------------------------------------------

export function remediationPullRequestsKey(
  projectId: string,
  params: ListRemediationPullRequestsParams = {},
) {
  return [
    "remediation",
    projectId,
    "pull-requests",
    { page: params.page ?? 1, page_size: params.page_size ?? 50 },
  ] as const;
}

/**
 * Do not retry a 4xx — a 403 (read-only), 404 (not visible), and 409 (not
 * opted in) are stable answers, not transient failures. Retry transport
 * failures (status 0) and 5xx once.
 */
function retryNon4xx(failureCount: number, error: Error): boolean {
  if (
    error instanceof ProblemError &&
    error.status >= 400 &&
    error.status < 500
  ) {
    return false;
  }
  return failureCount < 1;
}

// ---------------------------------------------------------------------------
// Dry-run — on-demand POST surfaced as a mutation.
// ---------------------------------------------------------------------------

export function useNpmDryRun(
  projectId: string,
): UseMutationResult<NpmDryRunResponse, Error, RemediationManifestBody | void> {
  return useMutation<NpmDryRunResponse, Error, RemediationManifestBody | void>({
    mutationFn: (body) => npmDryRun(projectId, body ?? {}),
    // Error surfaced locally (toast/inline) — keep the global error toast quiet.
    meta: { errorToast: false },
  });
}

// ---------------------------------------------------------------------------
// Create PR — mutation; invalidates the PR-list prefix on success.
// ---------------------------------------------------------------------------

export function useCreateNpmPullRequest(
  projectId: string,
): UseMutationResult<
  RemediationPullRequest | null,
  Error,
  RemediationManifestBody | void
> {
  const queryClient = useQueryClient();
  return useMutation<
    RemediationPullRequest | null,
    Error,
    RemediationManifestBody | void
  >({
    mutationFn: (body) => createNpmPullRequest(projectId, body ?? {}),
    meta: { errorToast: false },
    onSuccess: () => {
      // Invalidate every page of the list for this project so the new PR
      // (or the updated idempotent hit) shows up.
      void queryClient.invalidateQueries({
        queryKey: ["remediation", projectId, "pull-requests"],
      });
    },
  });
}

// ---------------------------------------------------------------------------
// PR list query.
// ---------------------------------------------------------------------------

export function useRemediationPullRequests(
  projectId: string | undefined,
  params: ListRemediationPullRequestsParams = {},
): UseQueryResult<RemediationPullRequestListPage, Error> {
  return useQuery({
    queryKey: remediationPullRequestsKey(projectId ?? "", params),
    queryFn: () => listRemediationPullRequests(projectId as string, params),
    enabled: typeof projectId === "string" && projectId.length > 0,
    staleTime: 30_000,
    retry: retryNon4xx,
  });
}
