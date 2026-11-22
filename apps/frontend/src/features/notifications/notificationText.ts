// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Title and body of an in-app notification, in the reader's language.
 *
 * The server stores English `title`/`body` and, since migration 0094, a
 * `message_key` naming the message plus the `params` it interpolates. When
 * the catalog has `notifications:message.<key>` the row is rendered from it;
 * otherwise (a row written before 0094, or a key this build does not know
 * yet) the stored English text is shown as before.
 *
 * Params are formatted here rather than on the server so the reader's locale
 * decides dates and severity names: `since` (ISO instant) and `expires_on`
 * (ISO date) become locale dates, and the per-severity counts of an SLA
 * breach become `breakdown`.
 */
import type { TFunction } from "i18next";

import type { NotificationItem } from "@/features/notifications/api/notificationsApi";

export interface NotificationText {
  title: string;
  body: string;
}

/** Same rule the server applies before storing a key. */
const MESSAGE_KEY_RE = /^[a-z][a-z0-9_]{0,63}$/;

/** Worst first, matching the server's own English breakdown. */
const SEVERITY_ORDER = ["critical", "high", "medium", "low"] as const;

function formatInstant(value: string, locale: string): string | null {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatDay(value: string, locale: string): string | null {
  // An ISO calendar date. Read and printed in UTC so the day does not move
  // with the reader's zone.
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(date);
}

// i18next reads these from its options object as lookup controls, so a param
// with one of these names would change which text is shown. The server drops
// the same names (notification_service._RESERVED_PARAM_NAMES).
const RESERVED_PARAM_NAMES = new Set([
  "lng",
  "lngs",
  "ns",
  "context",
  "ordinal",
  "replace",
  "interpolation",
  "defaultvalue",
]);

function interpolationValues(
  params: Record<string, unknown>,
  t: TFunction,
  locale: string,
): Record<string, string | number> {
  const values: Record<string, string | number> = {};
  for (const [name, value] of Object.entries(params)) {
    if (RESERVED_PARAM_NAMES.has(name)) continue;
    if (typeof value === "string" || typeof value === "number") {
      values[name] = value;
    } else if (typeof value === "boolean") {
      values[name] = String(value);
    }
  }
  if (typeof params.since === "string") {
    values.since = formatInstant(params.since, locale) ?? params.since;
  }
  if (typeof params.expires_on === "string") {
    values.expires_on = formatDay(params.expires_on, locale) ?? params.expires_on;
  }
  const parts = SEVERITY_ORDER.filter(
    (sev) => typeof params[sev] === "number",
  ).map((sev) => `${t(`common:risk.${sev}`)} ${params[sev] as number}`);
  if (parts.length > 0) {
    values.breakdown = parts.join(", ");
  }
  return values;
}

/**
 * Render `item` in the language `t` is bound to. `exists` is i18next's own
 * key check, passed in so a test can drive both branches.
 */
export function notificationText(
  item: Pick<NotificationItem, "title" | "body" | "message_key" | "params">,
  t: TFunction,
  exists: (key: string) => boolean,
  locale: string,
): NotificationText {
  const fallback = { title: item.title, body: item.body };
  const key = item.message_key;
  if (!key || !MESSAGE_KEY_RE.test(key)) return fallback;
  const base = `notifications:message.${key}`;
  if (!exists(`${base}.title`) || !exists(`${base}.body`)) return fallback;
  const values = interpolationValues(item.params ?? {}, t, locale);
  const title = t(`${base}.title`, values);
  const body = t(`${base}.body`, values);
  // A param the message needs but the row does not carry would surface as a
  // raw `{{name}}`. The stored text is the better answer for that row.
  if (title.includes("{{") || body.includes("{{")) return fallback;
  return { title, body };
}
