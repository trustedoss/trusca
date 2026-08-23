// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Search minimum-query-length contract (concurrency-scaling plan Q1).
 *
 * The floor lives in FOUR places: two backend service constants
 * (`services.search_service.MIN_QUERY_LEN` for the ⌘K palette,
 * `services.search_results_service.MIN_QUERY_LEN` for the full search page)
 * and their two frontend mirrors (`SEARCH_MIN_CHARS` in
 * `components/CommandMenu.tsx` and in
 * `features/search/api/useSearchResults.ts`). The backend half of this
 * contract (the two backend constants agreeing with each other) is
 * `apps/backend/tests/unit/test_catalog_contracts.py::
 * test_search_min_query_len_agrees_between_the_two_search_services`.
 *
 * Why this matters: a frontend constant lower than the backend's sends a
 * request the server just discards (wasted round-trip, and, before Q1, the
 * exact defect this plan unit closes: a 2-char query falling through to a
 * sequential scan). A frontend constant higher than the backend's hides
 * results the backend is willing to return. Same latent-drift class as the
 * FE↔BE catalog mirrors in `catalogMirrors.test.ts`, split into its own file
 * because this pair is cross-language (no `import` across the language
 * boundary is possible, so the backend source is read verbatim, the same
 * technique as `backendReviewFlagValues()` / `backendSlaStatusValues()` in
 * `catalogMirrors.test.ts`).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { SEARCH_MIN_CHARS as COMMAND_MENU_MIN_CHARS } from "@/components/CommandMenu";
import { SEARCH_MIN_CHARS as SEARCH_PAGE_MIN_CHARS } from "@/features/search/api/useSearchResults";

function backendMinQueryLen(relativePath: string): number {
  // Vitest root is apps/frontend, so the backend sits one level up. Reading
  // the source verbatim (rather than importing, which is impossible across
  // the language boundary) keeps the guard from drifting via a stale copy.
  const src = readFileSync(resolve(process.cwd(), relativePath), "utf-8");
  const assignment = /MIN_QUERY_LEN\s*=\s*(\d+)/.exec(src);
  if (!assignment) {
    throw new Error(`MIN_QUERY_LEN not found in ${relativePath}`);
  }
  return Number(assignment[1]);
}

describe("search minimum query length: FE-BE floor mirror (Q1)", () => {
  it("the palette's FE constant equals the palette's BE constant", () => {
    expect(COMMAND_MENU_MIN_CHARS).toBe(
      backendMinQueryLen("../backend/services/search_service.py"),
    );
  });

  it("the full-page FE constant equals the full-page BE constant", () => {
    expect(SEARCH_PAGE_MIN_CHARS).toBe(
      backendMinQueryLen("../backend/services/search_results_service.py"),
    );
  });

  it("both FE mirrors agree with each other", () => {
    // Deliberately independent constants (the palette and the full page are
    // separate endpoints by design), but a UI where 2 characters searches
    // one surface and not the other would read as broken, not intentional.
    expect(COMMAND_MENU_MIN_CHARS).toBe(SEARCH_PAGE_MIN_CHARS);
  });

  it("the floor is 3, matching the pg_trgm index minimum (migration 0043)", () => {
    // Pinned to the literal value, not just cross-side equality, so a future
    // change that moves all four copies together (losing the reason the
    // floor is 3 rather than some other number) still fails here.
    expect(COMMAND_MENU_MIN_CHARS).toBe(3);
    expect(SEARCH_PAGE_MIN_CHARS).toBe(3);
  });
});
