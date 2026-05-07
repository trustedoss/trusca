/**
 * TanStack Query hook for the admin Audit-Log search — Phase 4 PR #14.
 *
 * The query key carries the full filter tuple so a filter change cleanly
 * invalidates the previous page. Search results don't auto-refresh — the
 * audit log is append-only and the operator drives the cadence with the
 * "Refresh" button.
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
): UseQueryResult<AuditLogListPage, Error> {
  return useQuery({
    queryKey: adminAuditQueryKey(params),
    queryFn: () => searchAdminAudit(params),
  });
}
