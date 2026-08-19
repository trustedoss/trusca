// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Bulk onboarding mutations (N4).
 *
 * Both invalidate the whole admin-users subtree rather than patching rows: an
 * import creates people the current page has no entry for, and a paged list
 * cannot be patched into a correct state without refetching it anyway.
 */
import {
  useMutation,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";

import {
  bulkCreateAdminUsers,
  bulkDeactivateAdminUsers,
  type AdminUserCreateInput,
  type BulkResult,
} from "@/features/admin/api/adminUsersApi";

const ADMIN_USERS_KEY = ["admin", "users"] as const;

export function useBulkCreateAdminUsers(): UseMutationResult<
  BulkResult,
  Error,
  AdminUserCreateInput[]
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (rows: AdminUserCreateInput[]) => bulkCreateAdminUsers(rows),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ADMIN_USERS_KEY });
    },
  });
}

export function useBulkDeactivateAdminUsers(): UseMutationResult<
  BulkResult,
  Error,
  string[]
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userIds: string[]) => bulkDeactivateAdminUsers(userIds),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ADMIN_USERS_KEY });
    },
  });
}
