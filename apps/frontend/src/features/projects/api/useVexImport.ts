/**
 * useVexImport — v2.1 Track A (A3).
 *
 * Mutation for POST /v1/projects/{id}/vex/import. A VEX upload is a bulk-triage
 * write: a single document can auto-transition many findings (including into
 * `suppressed`). On success we invalidate the project's vulnerabilities list +
 * overview so the table status badges, "suppressed via VEX" filter, and risk
 * gauge reconcile from the server. We do NOT optimistically patch the list —
 * the server-computed summary (`matched/applied/skipped`) is the source of
 * truth and a single re-fetch is cheaper than reconciling an N-finding fan-out.
 *
 * Errors (403 not team_admin / 404 hidden / 413 too large / 422 malformed)
 * surface as {@link ProblemError} via the shared interceptor; the caller reads
 * `error.detail` / `error.status` for an actionable message.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  importVex,
  type VexImportSummary,
} from "@/features/projects/api/vexApi";

interface ImportVariables {
  file: File;
}

export function useVexImport(projectId: string | undefined) {
  const queryClient = useQueryClient();

  return useMutation<VexImportSummary, Error, ImportVariables>({
    mutationFn: ({ file }) => {
      if (!projectId) {
        throw new Error("VEX import requires a project id");
      }
      return importVex(projectId, file);
    },
    // Error surfaced locally (toast/inline) — keep the global error toast quiet.
    meta: { errorToast: false },
    onSuccess: () => {
      if (!projectId) return;
      // Reconcile every derived surface the import may have moved.
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "vulnerabilities"],
      });
      void queryClient.invalidateQueries({
        queryKey: ["projects", projectId, "overview"],
      });
      // Open drawers re-fetch their detail (status/provenance may have changed).
      void queryClient.invalidateQueries({
        queryKey: ["vulnerability_findings"],
      });
    },
  });
}
