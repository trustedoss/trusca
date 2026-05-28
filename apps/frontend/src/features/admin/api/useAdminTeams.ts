/**
 * useAdminTeams + useAdminTeam — query hooks for the admin Teams surface.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminTeam,
  listAdminTeams,
  type AdminTeamDetail,
  type AdminTeamListParams,
  type AdminTeamListResponse,
} from "@/features/admin/api/adminTeamsApi";

export function adminTeamsQueryKey(params: AdminTeamListParams) {
  return [
    "admin",
    "teams",
    {
      page: params.page ?? 1,
      page_size: params.page_size ?? 50,
      search: (params.search ?? "").trim(),
    },
  ] as const;
}

export function adminTeamQueryKey(teamId: string) {
  return ["admin", "teams", "detail", teamId] as const;
}

export function useAdminTeams(
  params: AdminTeamListParams,
): UseQueryResult<AdminTeamListResponse, Error> {
  return useQuery({
    queryKey: adminTeamsQueryKey(params),
    queryFn: () => listAdminTeams(params),
  });
}

export function useAdminTeam(
  teamId: string | null,
): UseQueryResult<AdminTeamDetail, Error> {
  return useQuery({
    queryKey: adminTeamQueryKey(teamId ?? ""),
    queryFn: () => getAdminTeam(teamId as string),
    enabled: typeof teamId === "string" && teamId.length > 0,
  });
}
