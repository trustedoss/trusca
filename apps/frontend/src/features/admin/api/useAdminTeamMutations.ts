/**
 * Mutations for the admin Teams surface — Phase 4 PR #13.
 *
 * Mirrors the user-mutation file: server-confirmed (no optimism), invalidate
 * `["admin", "teams"]` and update detail cache on success. Delete additionally
 * removes the open detail entry from cache.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { adminTeamQueryKey } from "@/features/admin/api/useAdminTeams";
import {
  addTeamMember,
  createTeam,
  deleteTeam,
  removeTeamMember,
  updateTeam,
  type AdminTeamCreatePayload,
  type AdminTeamDetail,
  type AdminTeamMemberAddPayload,
  type AdminTeamUpdatePayload,
} from "@/features/admin/api/adminTeamsApi";

function invalidateAll(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["admin", "teams"] });
}

export function useCreateTeam() {
  const queryClient = useQueryClient();
  return useMutation<AdminTeamDetail, Error, AdminTeamCreatePayload>({
    mutationFn: (payload) => createTeam(payload),
    onSuccess: (data) => {
      queryClient.setQueryData(adminTeamQueryKey(data.id), data);
      invalidateAll(queryClient);
    },
  });
}

export function useUpdateTeam() {
  const queryClient = useQueryClient();
  return useMutation<
    AdminTeamDetail,
    Error,
    { teamId: string; payload: AdminTeamUpdatePayload }
  >({
    mutationFn: ({ teamId, payload }) => updateTeam(teamId, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(adminTeamQueryKey(data.id), data);
      invalidateAll(queryClient);
    },
  });
}

export function useDeleteTeam() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, { teamId: string }>({
    mutationFn: ({ teamId }) => deleteTeam(teamId),
    onSuccess: (_data, vars) => {
      queryClient.removeQueries({ queryKey: adminTeamQueryKey(vars.teamId) });
      invalidateAll(queryClient);
    },
  });
}

export function useAddTeamMember() {
  const queryClient = useQueryClient();
  return useMutation<
    AdminTeamDetail,
    Error,
    { teamId: string; payload: AdminTeamMemberAddPayload }
  >({
    mutationFn: ({ teamId, payload }) => addTeamMember(teamId, payload),
    onSuccess: (data) => {
      queryClient.setQueryData(adminTeamQueryKey(data.id), data);
      invalidateAll(queryClient);
      // Also nudge the users list since membership counts changed.
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
}

export function useRemoveTeamMember() {
  const queryClient = useQueryClient();
  return useMutation<
    AdminTeamDetail,
    Error,
    { teamId: string; userId: string }
  >({
    mutationFn: ({ teamId, userId }) => removeTeamMember(teamId, userId),
    onSuccess: (data) => {
      queryClient.setQueryData(adminTeamQueryKey(data.id), data);
      invalidateAll(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
}
