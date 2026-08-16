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
 * of these read aggregates over every project in scope.
 *
 * Refreshing instead happens at the two moments a stale number would be seen
 * and believed. Returning to the tab refetches past the stale time, which is
 * an explicit opt-in here because the app turns `refetchOnWindowFocus` off
 * globally. And the mutations that change these numbers invalidate the keys
 * directly: approvals in `useApprovals.ts`, scans in `useCancelScan.ts`.
 *
 * The sidebar itself never unmounts on navigation, so nothing refetches from
 * a route change alone. That is deliberate rather than overlooked: three
 * requests on every screen change is the polling this rules out, wearing a
 * different name.
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
      refetchOnWindowFocus: true,
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
