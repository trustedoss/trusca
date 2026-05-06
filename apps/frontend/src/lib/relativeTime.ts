/**
 * Lightweight relative-time formatter — Phase 3 PR #11.
 *
 * `date-fns/formatDistanceToNow` would be a natural pick, but `date-fns` is
 * not in the dependency tree (and CLAUDE.md PR #11 brief forbids adding new
 * top-level deps for a small helper). We rely on the platform's
 * `Intl.RelativeTimeFormat` instead — every modern browser ships it.
 *
 * Mirrors the buckets `formatDistanceToNow` would pick: "just now" under a
 * minute, then minutes / hours / days / weeks / months / years. Future
 * timestamps render as positive relatives ("in 5 minutes").
 *
 * Accepts ISO8601 strings (the wire shape used by every backend timestamp
 * column) and returns the localized string for the active i18n locale.
 *
 * Returns `"—"` for null / unparseable input — the table renders the same
 * em-dash for missing scalar values, so the dash doubles as our fallback.
 */

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

const FALLBACK = "—";

/**
 * Render `value` as a relative time vs. `now` (defaults to wall clock).
 *
 * @param value ISO8601 datetime string. `null` / `undefined` → "—".
 * @param locale Optional BCP-47 tag. When omitted, the runtime picks up the
 *   active page language (`document.documentElement.lang` or undefined).
 *   Tests pass an explicit locale to make output deterministic.
 * @param now Reference timestamp; defaults to `Date.now()`. Tests pin this.
 */
export function formatRelativeToNow(
  value: string | null | undefined,
  locale?: string,
  now: number = Date.now(),
): string {
  if (value == null || value === "") return FALLBACK;
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return FALLBACK;

  // Intl.RelativeTimeFormat is widely supported (Edge 79+, Safari 14+,
  // Firefox 65+, Chrome 71+). The fallback path stringifies the absolute
  // value as an additional safety net.
  if (typeof Intl === "undefined" || typeof Intl.RelativeTimeFormat !== "function") {
    return new Date(ts).toISOString();
  }

  const diffMs = ts - now; // negative when in the past
  const absMs = Math.abs(diffMs);
  const sign = diffMs <= 0 ? -1 : 1;

  const fmt = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

  if (absMs < MINUTE) {
    return fmt.format(0, "second");
  }
  if (absMs < HOUR) {
    return fmt.format(sign * Math.round(absMs / MINUTE), "minute");
  }
  if (absMs < DAY) {
    return fmt.format(sign * Math.round(absMs / HOUR), "hour");
  }
  if (absMs < WEEK) {
    return fmt.format(sign * Math.round(absMs / DAY), "day");
  }
  if (absMs < MONTH) {
    return fmt.format(sign * Math.round(absMs / WEEK), "week");
  }
  if (absMs < YEAR) {
    return fmt.format(sign * Math.round(absMs / MONTH), "month");
  }
  return fmt.format(sign * Math.round(absMs / YEAR), "year");
}
