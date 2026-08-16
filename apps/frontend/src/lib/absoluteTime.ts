// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * One format for every absolute instant the product renders (B3).
 *
 * Scope note: the date-only badges (`EolBadge`, `KevBadge`, `SlaBadge`) still
 * print the wire value. They are not instants and not on this unit's list;
 * the Vulnerabilities table therefore still shows a formatted Discovered
 * column beside a raw SLA Due date.
 *
 * Seven private copies of this had grown, in `RelativeTime`, `releaseLabel`,
 * `MaliciousPanel`, `RecentScansTable`, `RemediationTab`, `SbomTab` and
 * `PortfolioGrid`, and three of them passed no locale at all.
 * `toLocaleString()` with no locale follows the browser, not the language the
 * app is running in, so a Korean session on an English-locale browser read
 * English dates beside Korean labels.
 *
 * The other half of the problem was the timezone. `toLocaleString` renders in
 * the browser's zone and says nothing about which one, while the audit log
 * printed its raw UTC instant. The same moment therefore appeared as two
 * different wall-clock times on two screens with nothing to explain the gap.
 * Every absolute instant this module renders carries its zone.
 *
 * Date-only values (a release date, an end-of-life date) are a different
 * thing: they name a day, not an instant, so attaching a zone to them would
 * assert a precision the value does not have.
 */

// What every caller gets for a value that is absent or unparseable. Defined
// in `format` and re-exported here: two exports of the same name with the
// same value is a coin toss for whoever reaches for one.
export { ABSENT } from "@/lib/format";
import { ABSENT } from "@/lib/format";

function parse(value: string | null | undefined): Date | null {
  if (value == null || value === "") return null;
  const ts = Date.parse(value);
  return Number.isNaN(ts) ? null : new Date(ts);
}

/**
 * Build a formatter, falling back to the runtime's own locale.
 *
 * `Intl.DateTimeFormat` throws a RangeError on a malformed tag, and a locale
 * reaches us from stored preferences. A timestamp rendered in the wrong
 * language is a much smaller problem than a screen that fails to render, and
 * `releaseLabel` carried this same defence before it was folded in here.
 */
function formatter(
  locale: string | undefined,
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormat {
  try {
    return new Intl.DateTimeFormat(locale, options);
  } catch {
    return new Intl.DateTimeFormat(undefined, options);
  }
}

/**
 * An instant, in the reader's own timezone, saying which one that is.
 *
 * e.g. "08/14/2026, 09:30 AM UTC+9" / "2026. 08. 14. 오전 09:30 UTC+9".
 *
 * The offset comes from `shortOffset` rather than being computed here, so it
 * stays right across a daylight-saving boundary and in zones whose offset is
 * not a whole hour. Only the leading token is rewritten: the platform prints
 * that offset as "GMT+9" in every locale this product ships, and UTC is what
 * the people reading these screens write.
 */
export function formatAbsoluteTime(
  value: string | null | undefined,
  locale?: string,
): string {
  const date = parse(value);
  if (date === null) return ABSENT;
  const rendered = formatter(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "shortOffset",
  }).format(date);
  return rendered.replace(/\bGMT\b/, "UTC");
}

/** `2026-08-14`, the shape the backend sends for a `date` column. */
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * A day, without a time and without a zone.
 *
 * For values that name a date rather than an instant. Attaching a zone here
 * would claim the value pins down a moment, which it does not.
 *
 * A date-only string is formatted in UTC because that is the zone
 * `Date.parse` read it in: `2026-08-14` becomes midnight UTC, and rendering
 * that in a zone west of Greenwich would print the 13th. An instant passed
 * here still renders as the reader's own day, which is what it means.
 */
export function formatAbsoluteDate(
  value: string | null | undefined,
  locale?: string,
): string {
  const date = parse(value);
  if (date === null) return ABSENT;
  return formatter(locale, {
    year: "numeric",
    month: "short",
    day: "numeric",
    ...(DATE_ONLY.test(value as string) ? { timeZone: "UTC" } : {}),
  }).format(date);
}
