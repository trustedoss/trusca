/**
 * TanStack Query hook for the admin Disk telemetry — Phase 4 PR #14.
 *
 * Polls every 30s by default so an operator who lands on the page sees
 * a steady refresh without a manual click. The shape is small (≤ 4
 * cards) so the polling cost is negligible.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  getAdminDisk,
  type AdminDiskOut,
} from "@/features/admin/disk/api/adminDiskApi";

export function adminDiskQueryKey() {
  return ["admin", "disk"] as const;
}

export function useAdminDisk(options?: {
  refetchIntervalMs?: number | false;
}): UseQueryResult<AdminDiskOut, Error> {
  return useQuery({
    queryKey: adminDiskQueryKey(),
    queryFn: () => getAdminDisk(),
    refetchInterval: options?.refetchIntervalMs ?? 30_000,
  });
}
