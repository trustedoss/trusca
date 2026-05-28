/**
 * useAdminUser — fetch a single user's admin detail (membership list, scan
 * count, activity timestamps). Skip-fetches when `userId` is null so the
 * drawer hook is safe to mount with no selection.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminUser,
  type AdminUserDetail,
} from "@/features/admin/api/adminUsersApi";

export function adminUserQueryKey(userId: string) {
  return ["admin", "users", "detail", userId] as const;
}

export function useAdminUser(
  userId: string | null,
): UseQueryResult<AdminUserDetail, Error> {
  return useQuery({
    queryKey: adminUserQueryKey(userId ?? ""),
    queryFn: () => getAdminUser(userId as string),
    enabled: typeof userId === "string" && userId.length > 0,
  });
}
