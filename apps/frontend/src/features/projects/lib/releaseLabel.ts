// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
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
import { formatAbsoluteDate } from "@/lib/absoluteTime";

export function releaseLabel(
  release: ReleaseSnapshot,
  locale: string,
): string {
  if (release.release && release.release.trim().length > 0) {
    return release.release;
  }
  // B3: this file used to carry its own `formatAbsoluteDate`, same name as
  // the shared one and different rules, which is a trap for whoever reads
  // one and assumes the other.
  return formatAbsoluteDate(release.created_at, locale);
}
