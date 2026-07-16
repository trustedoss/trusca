/**
 * useBulkTransitionVulnerabilities — W2 #33b.
 *
 * Mutation wrapper around `POST /v1/projects/{id}/vulnerabilities:bulk-transition`.
 *
 * Why no optimistic write here (unlike `useUpdateVulnerabilityStatus`)?
 *   - The bulk envelope is always 200 OK; per-row outcomes are data, not
 *     transport errors. The UI can't predict per-row success without
 *     duplicating the backend's matrix + role logic — getting that wrong
 *     would make the optimistic write LIE (showing rows as "analyzing" that
 *     ultimately failed 422).
 *   - The single-row hook's optimism shines because PATCH is binary
 *     (success or rollback). Bulk is partial: a clean snapshot/rollback
 *     model doesn't fit the per-row mixed outcome.
 *
 * Instead we invalidate the project's vulnerabilities list and any detail
 * caches whose finding was touched, so the UI reconciles from the server.
 * The list query is per-(filter-tuple) keyed; invalidating the whole
 * `["projects", projectId, "vulnerabilities"]` subtree is the right blast
 * radius — narrower keys would miss other open tabs / filters.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { vulnerabilityKey } from "@/features/projects/api/useVulnerability";
import {
  bulkTransitionVulnerabilities,
  type BulkStatusResponse,
  type BulkStatusUpdateBody,
} from "@/features/projects/api/vulnerabilitiesApi";

interface MutationVariables {
  projectId: string;
  body: BulkStatusUpdateBody;
}

export function useBulkTransitionVulnerabilities() {
  const queryClient = useQueryClient();

  return useMutation<BulkStatusResponse, Error, MutationVariables>({
    mutationFn: ({ projectId, body }) =>
      bulkTransitionVulnerabilities(projectId, body),

    // Error surfaced locally (toast/inline) — keep the global error toast quiet.
    meta: { errorToast: false },

    onSuccess: (response, { projectId }) => {
      // Reconcile from the server: invalidate the project's vulnerabilities
      // list (any filter tuple under it) so every visible page re-fetches
      // with the post-bulk state.
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "vulnerabilities"],
      });
      // Also invalidate detail caches for the rows that actually transitioned
      // — keeps an open drawer in sync the moment the user closes the bulk
      // toast and re-opens a row.
      for (const result of response.results) {
        if (!result.success) continue;
        void queryClient.invalidateQueries({
          queryKey: vulnerabilityKey(result.finding_id),
        });
      }
    },
  });
}
