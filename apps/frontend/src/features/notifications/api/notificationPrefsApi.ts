/**
 * Notification-preferences REST surface — Phase 6 chore A2.
 *
 * Wire shape (chore A2 contract):
 *   GET /v1/users/me/notification-prefs → NotificationPrefs
 *   PUT /v1/users/me/notification-prefs → echoes saved row
 */
import { api } from "@/lib/api";

export interface NotificationPrefs {
  email_enabled: boolean;
  slack_enabled: boolean;
  teams_enabled: boolean;
  in_app_enabled: boolean;
}

export async function getPrefs(): Promise<NotificationPrefs> {
  const { data } = await api.get<NotificationPrefs>(
    "/v1/users/me/notification-prefs",
  );
  return data;
}

export async function updatePrefs(
  prefs: NotificationPrefs,
): Promise<NotificationPrefs> {
  const { data } = await api.put<NotificationPrefs>(
    "/v1/users/me/notification-prefs",
    prefs,
  );
  return data;
}
