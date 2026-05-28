/**
 * useApiKeys — chore C (`/integrations` UI).
 *
 * TanStack Query hook for the paginated API-key list. Backed by
 * `GET /v1/api-keys` (apps/backend/api/v1/api_keys.py:151-186). Server state
 * only — never mirror this into Zustand.
 *
 * Mutations live alongside the page component so they can invalidate this
 * exact query key on success.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { listApiKeys } from "@/lib/apiKeysApi";
import type { APIKeyListPage, ListAPIKeysParams } from "@/types/apiKey";

export function apiKeysQueryKey(params: ListAPIKeysParams) {
  return [
    "api-keys",
    "list",
    {
      scope: params.scope ?? null,
      team_id: params.team_id ?? null,
      project_id: params.project_id ?? null,
      include_revoked: params.include_revoked ?? false,
      page: params.page ?? 1,
      page_size: params.page_size ?? 20,
    },
  ] as const;
}

export function useApiKeys(
  params: ListAPIKeysParams,
): UseQueryResult<APIKeyListPage, Error> {
  return useQuery({
    queryKey: apiKeysQueryKey(params),
    queryFn: () => listApiKeys(params),
    // Same staleness window as the rest of the portal (CLAUDE.md "Server
    // state — Stale time defaults to 30 s").
    staleTime: 30_000,
  });
}
