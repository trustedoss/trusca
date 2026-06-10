/**
 * TanStack Query hook for the admin Audit-Log search — Phase 4 PR #14.
 *
 * The query key carries the full filter tuple so a filter change cleanly
 * invalidates the previous page.
 *
 * L-15: the list polls every 2.5s by default so a fresh mutation shows up
 * without a manual refresh (the admin guide promises ~1s visibility after
 * a write). `refetchIntervalInBackground` stays at its default (false), so
 * a backgrounded tab does not poll. The manual "Refresh" button remains as
 * an immediate override.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  searchAdminAudit,
  type AuditLogListPage,
  type AuditSearchParams,
} from "@/features/admin/audit/api/adminAuditApi";

export function adminAuditQueryKey(params: AuditSearchParams) {
  return [
    "admin",
    "audit",
    {
      actor_user_id: params.actor_user_id ?? null,
      target_table: params.target_table ?? null,
      action: params.action ?? null,
      from: params.from ?? null,
      to: params.to ?? null,
      q: params.q ?? null,
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
    },
  ] as const;
}

export function useAdminAudit(
  params: AuditSearchParams,
  options?: { refetchIntervalMs?: number | false },
): UseQueryResult<AuditLogListPage, Error> {
  return useQuery({
    queryKey: adminAuditQueryKey(params),
    queryFn: () => searchAdminAudit(params),
    refetchInterval: options?.refetchIntervalMs ?? 2_500,
  });
}
