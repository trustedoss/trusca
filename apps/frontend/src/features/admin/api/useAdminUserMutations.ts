/**
 * Mutations for the admin Users surface — Phase 4 PR #13.
 *
 * Each mutation invalidates:
 *   - The list query (`["admin", "users", { … }]`) so paginated tables refresh.
 *   - The specific user's detail query (`["admin", "users", "detail", id]`)
 *     so the open drawer reflects the new state.
 *
 * No optimistic update for these flows — admin actions are infrequent and a
 * server confirmation is preferable to rolling back a user-visible state
 * change on a 422 (e.g. last-super-admin-protected). The brief calls out the
 * `last_super_admin_protected` / `cannot_modify_self` extensions explicitly
 * which makes server-confirmed UX safer.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { adminUserQueryKey } from "@/features/admin/api/useAdminUser";
import {
  activateUser,
  deactivateUser,
  requestPasswordReset,
  updateUserRole,
  type AdminUserDetail,
  type RoleUpdatePayload,
} from "@/features/admin/api/adminUsersApi";

function invalidateAll(queryClient: ReturnType<typeof useQueryClient>) {
  // Catch every paginated cache entry by prefix.
  void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
}

export function useUpdateUserRole() {
  const queryClient = useQueryClient();
  return useMutation<
    AdminUserDetail,
    Error,
    { userId: string; payload: RoleUpdatePayload }
  >({
    mutationFn: ({ userId, payload }) => updateUserRole(userId, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(adminUserQueryKey(data.id), data);
      invalidateAll(queryClient);
    },
  });
}

export function useDeactivateUser() {
  const queryClient = useQueryClient();
  return useMutation<AdminUserDetail, Error, { userId: string }>({
    mutationFn: ({ userId }) => deactivateUser(userId),
    onSuccess: (data) => {
      queryClient.setQueryData(adminUserQueryKey(data.id), data);
      invalidateAll(queryClient);
    },
  });
}

export function useActivateUser() {
  const queryClient = useQueryClient();
  return useMutation<AdminUserDetail, Error, { userId: string }>({
    mutationFn: ({ userId }) => activateUser(userId),
    onSuccess: (data) => {
      queryClient.setQueryData(adminUserQueryKey(data.id), data);
      invalidateAll(queryClient);
    },
  });
}

export function useResetUserPassword() {
  return useMutation<void, Error, { userId: string }>({
    mutationFn: ({ userId }) => requestPasswordReset(userId),
  });
}
