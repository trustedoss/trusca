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
 * The wire layer can hand us `null` (no EPSS entry for the CVE). We render an
 * em dash for that case rather than "0%", because absence and "0% likely" are
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

/**
 * Format an EPSS percentile (0–1 rank) as a "Top N%" string, e.g.
 * `0.91 → "Top 9%"` (the score outranks 91% of CVEs → it sits in the top 9%).
 * Returns `null` for missing / out-of-range input.
 *
 * We round to a whole percent — sub-percent precision on a rank is noise for a
 * triage decision. A percentile of exactly 1 (top 0%) is clamped to "Top <1%"
 * so we never claim "Top 0%".
 */
export function formatEpssPercentile(
  percentile: number | null | undefined,
): string | null {
  if (percentile == null || !Number.isFinite(percentile)) return null;
  if (percentile < 0 || percentile > 1) return null;
  const topPct = (1 - percentile) * 100;
  if (topPct < 1 && topPct > 0) return "Top <1%";
  return `Top ${Math.round(topPct)}%`;
}
