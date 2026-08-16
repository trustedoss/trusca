// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Live counts for the sidebar (C1).
 *
 * The sidebar named destinations but said nothing about them, so the two rows
 * that carry work waiting on a person - Scans and Approvals - looked exactly
 * like the ones that do not. Someone had to open Approvals to learn whether
 * there was anything to approve.
 *
 * Deliberately not polled. §8.5-5 of the plan rules out new polling, and both
 * of these read aggregates over every project in scope. React Query's own
 * defaults are the refresh mechanism: a navigation or a window focus past the
 * stale time refetches, which is the moment the number is actually looked at.
 */
import { useQueries } from "@tanstack/react-query";

import { useActionQueue } from "@/features/dashboard/api/actionQueue";
import { scansQueryKey } from "@/features/scans/useScans";
import { listMyScans } from "@/lib/projectsApi";

export type NavBadgeKey = "scans" | "approvals";

/**
 * A count is absent until it is known. Rendering `0` from a cold or failed
 * cache would answer "is there anything waiting?" with "no" on no evidence,
 * which is worse than the sidebar saying nothing at all.
 */
export type NavBadgeCounts = Partial<Record<NavBadgeKey, number>>;

/**
 * The scan states that mean "something is happening right now". `succeeded`
 * and `failed` are history, and a badge counting them would never fall back
 * to nothing.
 *
 * `GET /v1/scans` takes one status, so this costs one request per state. Both
 * ask for a single row and read `total` off the envelope.
 */
const ACTIVE_SCAN_STATUSES = ["running", "queued"] as const;

const BADGE_STALE_TIME_MS = 60_000;

/** Page size 1: the rows are thrown away, only the `total` is wanted. */
const COUNT_ONLY = { page: 1, size: 1 } as const;

export function useNavBadges(): NavBadgeCounts {
  // The dashboard's own hook, key and stale time included, so the shell and
  // the dashboard share one cache entry rather than each fetching this.
  const approvals = useActionQueue().data?.pending_approvals;

  const scanResults = useQueries({
    queries: ACTIVE_SCAN_STATUSES.map((status) => ({
      queryKey: scansQueryKey({ status, ...COUNT_ONLY }),
      queryFn: () => listMyScans({ status, ...COUNT_ONLY }),
      staleTime: BADGE_STALE_TIME_MS,
    })),
  });

  // Partial scan data would understate the count, and a sidebar that says 2
  // while 5 are running is worse than one that says nothing, so both states
  // have to have answered.
  const scanTotals = scanResults.map((result) => result.data?.total);
  const scans = scanTotals.every((total) => typeof total === "number")
    ? (scanTotals as number[]).reduce((sum, total) => sum + total, 0)
    : undefined;

  return { approvals, scans };
}
