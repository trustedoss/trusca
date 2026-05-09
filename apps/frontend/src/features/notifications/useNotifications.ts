/**
 * useNotifications — TanStack Query surface for the in-app notification
 * center (Phase 6 chore A2).
 *
 * Conventions follow the rest of the codebase:
 *   - Server state lives here only; Zustand is reserved for UI state per
 *     CLAUDE.md.
 *   - Query keys are tuples prefixed by domain (`["notifications", ...]`)
 *     so a single invalidation call clears every dependent cache (list
 *     pages + unread badge).
 *   - Mutations onSuccess invalidate by prefix so the bell badge re-fetches
 *     immediately after the user acks a notification.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import {
  getUnreadCount,
  listNotifications,
  markAllRead,
  markRead,
  type NotificationListParams,
  type NotificationListResponse,
  type UnreadCountResponse,
} from "@/features/notifications/api/notificationsApi";
import {
  getPrefs,
  updatePrefs,
  type NotificationPrefs,
} from "@/features/notifications/api/notificationPrefsApi";

export const NOTIFICATIONS_KEY = ["notifications"] as const;
export const NOTIFICATIONS_LIST_KEY = ["notifications", "list"] as const;
export const NOTIFICATIONS_UNREAD_KEY = ["notifications", "unread-count"] as const;
export const NOTIFICATION_PREFS_KEY = ["notifications", "prefs"] as const;

function invalidateAll(queryClient: ReturnType<typeof useQueryClient>) {
  // Prefix invalidation hits both the list pages and the unread badge.
  void queryClient.invalidateQueries({ queryKey: NOTIFICATIONS_KEY });
}

export function useNotifications(
  params: NotificationListParams,
): UseQueryResult<NotificationListResponse, Error> {
  return useQuery({
    queryKey: [...NOTIFICATIONS_LIST_KEY, params] as const,
    queryFn: () => listNotifications(params),
  });
}

/**
 * Bell-badge query. Polls every 60 s **only when the tab is visible**
 * (TanStack Query's `refetchIntervalInBackground: false` defaults already
 * pause the timer when the page is hidden — that's exactly the behaviour
 * we want here). Stale time is 30 s so a navigation back to the inbox
 * doesn't immediately re-fetch.
 */
export function useUnreadCount(
  options: { enabled?: boolean } = {},
): UseQueryResult<UnreadCountResponse, Error> {
  return useQuery({
    queryKey: NOTIFICATIONS_UNREAD_KEY,
    queryFn: () => getUnreadCount(),
    enabled: options.enabled ?? true,
    refetchInterval: 60_000,
    // The default is already false but spell it out so future maintainers
    // see why we don't poll while hidden — the prompt explicitly calls it
    // out as a perf/UX requirement.
    refetchIntervalInBackground: false,
    staleTime: 30_000,
  });
}

export function useMarkRead(): UseMutationResult<void, Error, string> {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => markRead(id),
    onSuccess: () => invalidateAll(queryClient),
  });
}

export function useMarkAllRead(): UseMutationResult<void, Error, void> {
  const queryClient = useQueryClient();
  return useMutation<void, Error, void>({
    mutationFn: () => markAllRead(),
    onSuccess: () => invalidateAll(queryClient),
  });
}

export function useNotificationPrefs(): UseQueryResult<
  NotificationPrefs,
  Error
> {
  return useQuery({
    queryKey: NOTIFICATION_PREFS_KEY,
    queryFn: () => getPrefs(),
  });
}

export function useUpdateNotificationPrefs(): UseMutationResult<
  NotificationPrefs,
  Error,
  NotificationPrefs
> {
  const queryClient = useQueryClient();
  return useMutation<NotificationPrefs, Error, NotificationPrefs>({
    mutationFn: (prefs) => updatePrefs(prefs),
    onSuccess: (saved) => {
      // Reflect the server's authoritative row back into cache so the
      // form's `defaultValues` re-derive on next render.
      queryClient.setQueryData<NotificationPrefs>(NOTIFICATION_PREFS_KEY, saved);
    },
  });
}
