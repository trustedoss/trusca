/**
 * useCancelScan — user-facing scan cancellation (PR-A3).
 *
 * TanStack Query mutation around `POST /v1/scans/{scan_id}/cancel` (PR-A1).
 * On success the backend returns the cancelled `ScanPublic`; we invalidate
 * every scan-list slice (`["scans"]` prefix used by `useScans`) and the
 * project list (latest-scan badge) so the queue + project rows reflect the
 * terminal state on the next page-turn. The single-scan WebSocket emits its
 * own terminal `cancelled` frame, so the progress bar settles without our
 * help — we don't poke it here.
 *
 * We deliberately do NOT do an optimistic write of `cancelled` into the
 * cache: the request is fast, cancellation can legally fail with 409 (the
 * scan finished between render and click), and an optimistic flip that has
 * to roll back reads as a UI glitch. Invalidate-on-success keeps the row
 * authoritative.
 */
import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";

import { cancelScan, type ScanPublic } from "@/lib/projectsApi";

export interface CancelScanVariables {
  scanId: string;
}

export function useCancelScan(): UseMutationResult<
  ScanPublic,
  Error,
  CancelScanVariables
> {
  const queryClient = useQueryClient();
  return useMutation<ScanPublic, Error, CancelScanVariables>({
    mutationFn: ({ scanId }) => cancelScan(scanId),
    // Error surfaced locally (toast/inline) — keep the global error toast quiet.
    meta: { errorToast: false },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["scans"] });
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}
