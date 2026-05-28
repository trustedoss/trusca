/**
 * useAdminBackups — TanStack Query surface for `/v1/admin/backup`.
 *
 * Mirrors the `useAdminUsers` shape: one `useQuery` for the list, plus
 * domain-named mutations that invalidate the list cache by prefix.
 *
 * Server state lives here only — Zustand is reserved for client UI state
 * (the typing-gate text, the confirm strip toggle, etc.) per CLAUDE.md.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  deleteBackup,
  downloadBackup,
  listBackups,
  triggerManualBackup,
  uploadRestore,
  type BackupListResponse,
  type BackupRestoreResponse,
  type BackupTriggerResponse,
} from "@/features/admin/api/adminBackupsApi";

export const ADMIN_BACKUPS_KEY = ["admin", "backups"] as const;

export function useAdminBackups(): UseQueryResult<BackupListResponse, Error> {
  return useQuery({
    queryKey: ADMIN_BACKUPS_KEY,
    queryFn: () => listBackups(),
  });
}

function invalidate(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ADMIN_BACKUPS_KEY });
}

export function useTriggerManualBackup(): UseMutationResult<
  BackupTriggerResponse,
  Error,
  void
> {
  const queryClient = useQueryClient();
  return useMutation<BackupTriggerResponse, Error, void>({
    mutationFn: () => triggerManualBackup(),
    onSuccess: () => {
      // The new artifact lands on disk asynchronously (Celery task) — the
      // list refetch will pick it up once the task writes the manifest.
      invalidate(queryClient);
    },
  });
}

export function useDownloadBackup(): UseMutationResult<void, Error, string> {
  return useMutation<void, Error, string>({
    mutationFn: (name) => downloadBackup(name),
  });
}

export function useDeleteBackup(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (name) => deleteBackup(name),
    onSuccess: () => invalidate(queryClient),
  });
}

export function useUploadRestore(): UseMutationResult<
  BackupRestoreResponse,
  Error,
  File
> {
  return useMutation<BackupRestoreResponse, Error, File>({
    mutationFn: (file) => uploadRestore(file),
  });
}
