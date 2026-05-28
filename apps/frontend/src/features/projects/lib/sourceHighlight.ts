/**
 * Per-line license-highlight projection — G3.3.
 *
 * Pure helpers that fold a file's `content` + `license_matches` into a flat
 * per-line model the viewer can render without re-scanning the match list for
 * every row. Kept framework-free so the highlight math is unit-tested in
 * isolation (no DOM, no React).
 *
 * A line is "highlighted" when any match's [start_line, end_line] (1-based,
 * inclusive) covers it. Overlapping matches accumulate so a tooltip can list
 * every license touching a line, deduped + ordered by descending score.
 */
import type { LicenseMatch } from "@/features/projects/api/sourceTreeApi";

export interface LineMatch {
  spdx_id: string;
  score: number | null;
}

export interface SourceLine {
  /** 1-based line number. */
  number: number;
  /** Raw line text (no trailing newline). */
  text: string;
  /** License matches covering this line (empty when none). */
  matches: LineMatch[];
}

/**
 * Split `content` into lines. A trailing newline does NOT create a phantom
 * empty final line (matches what an editor shows). Null content (binary)
 * yields an empty array.
 */
export function splitLines(content: string | null | undefined): string[] {
  if (content == null || content.length === 0) return content === "" ? [""] : [];
  // Normalise CRLF / CR so line counting matches the backend's line indices.
  const normalised = content.replace(/\r\n?/g, "\n");
  const parts = normalised.split("\n");
  // Drop a single trailing empty element produced by a terminating newline.
  if (parts.length > 1 && parts[parts.length - 1] === "") parts.pop();
  return parts;
}

/**
 * Dedupe matches that touch one line by spdx_id, keeping the highest score,
 * then order by descending score (nulls last) for a stable tooltip.
 */
function dedupeLineMatches(matches: LineMatch[]): LineMatch[] {
  const best = new Map<string, LineMatch>();
  for (const m of matches) {
    const prior = best.get(m.spdx_id);
    if (
      !prior ||
      (m.score ?? -1) > (prior.score ?? -1)
    ) {
      best.set(m.spdx_id, m);
    }
  }
  return [...best.values()].sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
}

/**
 * Build the per-line model. `content` is split into lines; each line gets the
 * deduped set of matches whose inclusive range covers its 1-based number.
 * Out-of-range match lines (e.g. a match end past EOF) are clamped silently so
 * a stale index never throws.
 */
export function buildSourceLines(
  content: string | null | undefined,
  matches: readonly LicenseMatch[] = [],
): SourceLine[] {
  const lines = splitLines(content);
  if (lines.length === 0) return [];

  // Bucket matches by line for O(content + matches) assembly rather than
  // O(content * matches).
  const byLine = new Map<number, LineMatch[]>();
  for (const m of matches) {
    const start = Math.max(1, Math.min(m.start_line, m.end_line));
    const end = Math.min(lines.length, Math.max(m.start_line, m.end_line));
    for (let n = start; n <= end; n += 1) {
      const bucket = byLine.get(n);
      const entry: LineMatch = { spdx_id: m.spdx_id, score: m.score };
      if (bucket) bucket.push(entry);
      else byLine.set(n, [entry]);
    }
  }

  return lines.map((text, idx) => {
    const number = idx + 1;
    const raw = byLine.get(number);
    return {
      number,
      text,
      matches: raw ? dedupeLineMatches(raw) : [],
    };
  });
}

/**
 * Format a one-line tooltip for a highlighted line's matches:
 * "MIT 99.5% · Apache-2.0". Scores render as a rounded percentage; null scores
 * drop the percentage. Returns an empty string when there are no matches.
 */
export function formatMatchTooltip(matches: readonly LineMatch[]): string {
  return matches
    .map((m) =>
      m.score == null ? m.spdx_id : `${m.spdx_id} ${Math.round(m.score)}%`,
    )
    .join(" · ");
}
