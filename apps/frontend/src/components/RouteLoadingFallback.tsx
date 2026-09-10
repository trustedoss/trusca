// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Route-level Suspense fallback (#421).
 *
 * `React.lazy()` route components need a fallback that renders before any
 * particular page's own loading state exists yet, so this cannot be a
 * page-specific skeleton (a table's row skeleton, a drawer's field
 * skeleton). It is deliberately generic: a page-shaped block (a title bar
 * plus a few content rows), not a spinner, matching this design system's
 * "skeleton placeholders, not spinners, for the top-level loading state"
 * rule (`components/ui/skeleton.tsx`).
 *
 * In practice this renders for one chunk fetch, typically well under the
 * time a skeleton needs to read as intentional rather than a flash; it
 * exists for the slow-connection / cold-cache case, not the common one.
 */
export function RouteLoadingFallback() {
  return (
    <div className="flex flex-col gap-4 p-6" data-testid="route-loading-fallback">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}
