/**
 * useObligation — Phase 3 PR #13.
 *
 * Lazy fetch for the obligation drawer. Only enabled while the drawer is
 * open and an obligation id is selected via the `?obligation=<id>` URL
 * param. Read-only domain: no mutation pairs with this query.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getObligation,
  type ObligationDetailResponse,
} from "@/features/projects/api/obligationsApi";

export function obligationKey(projectId: string, obligationId: string) {
  return ["projects", projectId, "obligations", obligationId] as const;
}

export function useObligation(
  projectId: string | undefined,
  obligationId: string | null | undefined,
): UseQueryResult<ObligationDetailResponse, Error> {
  return useQuery({
    queryKey: obligationKey(projectId ?? "", obligationId ?? ""),
    queryFn: () => getObligation(projectId as string, obligationId as string),
    enabled:
      typeof projectId === "string" &&
      projectId.length > 0 &&
      typeof obligationId === "string" &&
      obligationId.length > 0,
    staleTime: 30_000,
  });
}
