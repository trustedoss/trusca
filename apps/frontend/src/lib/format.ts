// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Number formatting for the screens people read (B4).
 *
 * Three problems, all of them the kind that looks like a typo rather than a
 * bug, which is why they survived:
 *
 *   - Counts reached the DOM as `String(n)`, so a portfolio with 12480 open
 *     findings rendered "12480" in a tile styled `tabular-nums` for exactly
 *     this kind of number.
 *   - Where a count WAS formatted, half the call sites used a bare
 *     `toLocaleString()`, which follows the browser rather than the language
 *     the app is running in. A Korean session on an English-locale browser
 *     got one separator convention in the trend card and another in the
 *     health panel beside it.
 *   - A delta of zero rendered with a sign. The trend card printed "+0 −0"
 *     on any quiet window, which reads as two separate movements rather
 *     than as nothing having happened.
 *
 * Scope note: scaling a byte count to a unit is not here. Five hand-rolled
 * `formatBytes` copies exist and one of them tops out at MB, so a 3 GB
 * archive reads "3072.0 MB"; consolidating them is its own piece of work.
 * (A raw byte count rendered as a plain number does come through
 * `formatNumber`, in the source viewer.) Neither are the counts interpolated
 * through i18next (`{{total}}` and friends), which would want a formatter
 * registered on the i18n instance and every affected string rewritten in
 * both locales.
 */

/** The minus sign, U+2212. ASCII hyphen is not a minus and reads narrower. */
const MINUS = "−";

/** What a caller renders for a value that is absent. */
// em-dash-allow: this is the product's own placeholder glyph, not prose
export const ABSENT = "—";

/** The shape `useTranslation` returns, narrowed to what the locale needs. */
interface LanguageSource {
  resolvedLanguage?: string;
  language: string;
}

/**
 * The language the app is running in, for a formatter.
 *
 * One expression rather than five: half the call sites read
 * `resolvedLanguage` alone, and where that comes back undefined the
 * formatter silently falls to the browser's locale, which is the exact
 * defect this module exists to remove.
 */
export function resolveLocale(i18n: LanguageSource): string {
  return i18n.resolvedLanguage ?? i18n.language;
}

function formatter(
  locale: string | undefined,
  options: Intl.NumberFormatOptions,
): Intl.NumberFormat {
  // A malformed tag throws a RangeError, and the locale reaches us from
  // stored preferences. A number in the wrong grouping convention beats a
  // screen that fails to render.
  try {
    return new Intl.NumberFormat(locale, options);
  } catch {
    return new Intl.NumberFormat(undefined, options);
  }
}

/**
 * A count, grouped for the reader's language.
 *
 * e.g. 12480 becomes "12,480" in English and "12,480" in Korean; the two
 * agree today, but the point is that the app decides rather than the
 * browser.
 */
export function formatNumber(
  value: number | null | undefined,
  locale?: string,
): string {
  // The placeholder, not zero. This panel's own trend series carries a note
  // that drawing zero for a value it failed to load is the one answer it
  // must never give, and a shared helper that quietly does so is a trap for
  // whoever reaches for it next.
  if (value == null || !Number.isFinite(value)) return ABSENT;
  return formatter(locale, {}).format(value);
}

/**
 * A movement, with its direction in the text rather than only in the colour.
 *
 * Zero has no direction, so it gets no sign. Rendering "+0" and "−0" beside
 * each other says two things happened and cancelled out, which is not what
 * an empty window means.
 */
export function formatSignedDelta(
  value: number | null | undefined,
  locale?: string,
): string {
  if (value == null || !Number.isFinite(value)) return ABSENT;
  if (value === 0) return formatNumber(0, locale);
  const magnitude = formatNumber(Math.abs(value), locale);
  return value > 0 ? `+${magnitude}` : `${MINUS}${magnitude}`;
}

/**
 * A percentage, given as 0..100 rather than 0..1.
 *
 * The unit sign comes from the locale rather than being appended, because
 * where it goes and whether a space precedes it is a property of the
 * language, not of the number.
 */
export function formatPercent(
  value: number | null | undefined,
  locale?: string,
  fractionDigits = 0,
): string {
  if (value == null || !Number.isFinite(value)) return ABSENT;
  return formatter(locale, {
    style: "percent",
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value / 100);
}
