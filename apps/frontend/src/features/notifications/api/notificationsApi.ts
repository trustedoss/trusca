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

/**
 * Closed in-app notification kind set — runtime mirror of the backend's
 * `models/notification.py::NOTIFICATION_KIND_VALUES` (Postgres enum
 * `notification_kind`), same order. PR-6 FE regression guards: the contract
 * test `tests/unit/contracts/catalogMirrors.test.ts` pins this array against
 * the shared fixture `tests/contracts/notification-kinds.json` so an enum
 * value added on the backend (H-5: `approval_state_changed` shipped in
 * migration 0030 while this union silently stayed at six) fails a PR-time
 * vitest instead of rendering a fallback icon + raw i18n key in production.
 */
export const NOTIFICATION_KINDS = [
  "scan_completed",
  "scan_failed",
  "cve_detected",
  "license_violation",
  "approval_pending",
  "policy_gate_failed",
  "approval_state_changed",
] as const;

export type NotificationKind = (typeof NOTIFICATION_KINDS)[number];

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
