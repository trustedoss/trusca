/**
 * Notifications REST surface — Phase 6 chore A2.
 *
 * Wire shape pinned in chore A2:
 *   GET    /v1/notifications                 → NotificationListResponse
 *   PATCH  /v1/notifications/{id}/read       → 204
 *   PATCH  /v1/notifications/read-all        → 204
 *   GET    /v1/notifications/unread-count    → { count: number }
 *
 * All endpoints require a JWT bearer token; the shared axios instance
 * attaches it. Errors surface as `ProblemError` (RFC 7807) so the UI can
 * branch on `err.problem.title` for tone-specific copy.
 */
import { api } from "@/lib/api";

export type NotificationKind =
  | "scan_completed"
  | "scan_failed"
  | "cve_detected"
  | "license_violation"
  | "approval_pending"
  | "policy_gate_failed";

export interface NotificationItem {
  id: string;
  kind: NotificationKind;
  title: string;
  body: string;
  link: string | null;
  target_table: string | null;
  target_id: string | null;
  read_at: string | null;
  created_at: string;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  total: number;
  unread_count: number;
  page: number;
  page_size: number;
}

export interface NotificationListParams {
  unread_only?: boolean;
  page?: number;
  page_size?: number;
}

export interface UnreadCountResponse {
  count: number;
}

export async function listNotifications(
  params: NotificationListParams = {},
): Promise<NotificationListResponse> {
  const { data } = await api.get<NotificationListResponse>(
    "/v1/notifications",
    {
      params: {
        unread_only: params.unread_only ?? false,
        page: params.page ?? 1,
        page_size: params.page_size ?? 20,
      },
    },
  );
  return data;
}

export async function markRead(id: string): Promise<void> {
  await api.patch(`/v1/notifications/${encodeURIComponent(id)}/read`);
}

export async function markAllRead(): Promise<void> {
  await api.patch("/v1/notifications/read-all");
}

export async function getUnreadCount(): Promise<UnreadCountResponse> {
  const { data } = await api.get<UnreadCountResponse>(
    "/v1/notifications/unread-count",
  );
  return data;
}
