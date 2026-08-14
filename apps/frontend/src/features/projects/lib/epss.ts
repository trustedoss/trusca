// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * EPSS formatting helpers — v2.1 "EPSS UI first-class".
 *
 * EPSS (Exploit Prediction Scoring System) ships two numbers, both in [0, 1]:
 *
 *   - `epss_score`      probability the CVE is exploited in the wild over the
 *                       next 30 days. Read as a percentage.
 *   - `epss_percentile` rank of that score among all scored CVEs. Read as
 *                       "in the top N% most likely to be exploited".
 *
 * The wire layer can hand us `null` (no EPSS entry for the CVE). We render a
 * dash for that case rather than "0%", because absence and "0% likely" are
 * semantically different and conflating them would mislead a triager.
 *
 * Display decision (reported back to the team): the **score** is shown as a
 * percentage with one decimal (0.973 → "97.3%"). A percentage reads faster
 * for a probability than a bare decimal, and one decimal keeps the column
 * narrow while still distinguishing 97.3% from 97.9%. The **percentile** is a
 * secondary signal, phrased as "Top {{n}}%" (0.91 → "Top 9%"), surfaced in the
 * tooltip / drawer rather than the main cell.
 */

/** The placeholder rendered for a missing EPSS value. */
export const EPSS_EMPTY = "—";

/**
 * Format an EPSS score (0–1 probability) as a one-decimal percentage string,
 * e.g. `0.973 → "97.3%"`. Returns `null` for missing / out-of-range input so
 * callers can branch to the empty placeholder + the right test id.
 */
export function formatEpssScore(score: number | null | undefined): string | null {
  if (score == null || !Number.isFinite(score)) return null;
  if (score < 0 || score > 1) return null;
  return `${(score * 100).toFixed(1)}%`;
}

/** A translation key plus its interpolation values. */
export interface EpssPercentileLabel {
  key: string;
  params: Record<string, number>;
}

/**
 * Describe an EPSS percentile (0–1 rank) as the translation key that renders
 * it, e.g. `0.91` → `vulnerabilities.epss.top_percentile` with `{ value: 9 }`
 * (the score outranks 91% of CVEs, so it sits in the top 9%). Returns `null`
 * for missing / out-of-range input.
 *
 * This returns a key rather than a finished string because the wording is
 * user-facing and has to follow the UI language. It used to build "Top N%" in
 * code, which reached Korean screens in English.
 *
 * We round to a whole percent, since sub-percent precision on a rank is noise
 * for a triage decision. A rank between 0 and 1 percent takes its own key, so
 * rounding never prints "top 0%" for a CVE that is not at the very top; a rank
 * of exactly 1 does print it, and there it is literally true.
 */
export function epssPercentileLabel(
  percentile: number | null | undefined,
): EpssPercentileLabel | null {
  if (percentile == null || !Number.isFinite(percentile)) return null;
  if (percentile < 0 || percentile > 1) return null;
  const topPct = (1 - percentile) * 100;
  if (topPct < 1 && topPct > 0) {
    return { key: "vulnerabilities.epss.top_percentile_sub1", params: {} };
  }
  return {
    key: "vulnerabilities.epss.top_percentile",
    params: { value: Math.round(topPct) },
  };
}
