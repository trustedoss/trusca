/**
 * releaseLabel — feature #28 Phase 1 (release snapshot viewing).
 *
 * Resolves the human label for a release snapshot, shared by the Releases tab
 * rows and the historical-snapshot banner so both render the exact same text:
 *
 *   1. the release name when present (e.g. `v1.2.3`),
 *   2. otherwise the snapshot's formatted creation date,
 *   3. otherwise an em-dash (unparseable timestamp — should never happen).
 *
 * Kept in a plain module (not the component file) so React Fast Refresh stays
 * happy and the helper can be imported without pulling in the tab's JSX.
 */
import type { ReleaseSnapshot } from "@/features/projects/api/releasesApi";

export function releaseLabel(
  release: ReleaseSnapshot,
  locale: string,
): string {
  if (release.release && release.release.trim().length > 0) {
    return release.release;
  }
  const formatted = formatAbsoluteDate(release.created_at, locale);
  return formatted ?? "—";
}

function formatAbsoluteDate(value: string, locale: string): string | null {
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) return null;
  try {
    return new Date(ts).toLocaleDateString(locale, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return new Date(ts).toISOString().slice(0, 10);
  }
}
