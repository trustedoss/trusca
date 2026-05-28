/**
 * useAdminUsers — paginated list query for /v1/admin/users.
 *
 * Server state lives in TanStack Query — no Zustand. The query key includes
 * the full filter tuple so a filter or page change invalidates cleanly.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  listAdminUsers,
  type AdminUserListParams,
  type AdminUserListResponse,
} from "@/features/admin/api/adminUsersApi";

export function adminUsersQueryKey(params: AdminUserListParams) {
  return [
    "admin",
    "users",
    {
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
      role: params.role ?? null,
      active: params.active ?? null,
      search: (params.search ?? "").trim(),
    },
  ] as const;
}

export function useAdminUsers(
  params: AdminUserListParams,
): UseQueryResult<AdminUserListResponse, Error> {
  return useQuery({
    queryKey: adminUsersQueryKey(params),
    queryFn: () => listAdminUsers(params),
  });
}
