/**
 * useLicenseFinding — Phase 3 PR #12.
 *
 * Lazy fetch for the license drawer. Only enabled while the drawer is open
 * and a finding id is selected via the `?license=<id>` URL param.
 *
 * Read-only domain: no mutation pairs with this query, so unlike
 * `useVulnerability` we never need to write back from a PATCH response.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getLicenseFinding,
  type LicenseDetailResponse,
} from "@/features/projects/api/licensesApi";

export function licenseFindingKey(findingId: string) {
  return ["license_findings", findingId] as const;
}

export function useLicenseFinding(
  findingId: string | null | undefined,
): UseQueryResult<LicenseDetailResponse, Error> {
  return useQuery({
    queryKey: licenseFindingKey(findingId ?? ""),
    queryFn: () => getLicenseFinding(findingId as string),
    enabled: typeof findingId === "string" && findingId.length > 0,
    staleTime: 30_000,
  });
}
