/**
 * parseScopeFilter — Phase K (PR K-2).
 *
 * The scope-filter telemetry blob is worker-written JSONB surfaced verbatim
 * through `ScanPublic.metadata`; the parser must treat every level as
 * untrusted and collapse every "nothing to show" shape to `null` so the
 * summary-band note renders only on a positive drop count.
 */
import { describe, expect, it } from "vitest";

import { parseScopeFilter } from "@/features/projects/api/useScanScopeFilter";

describe("parseScopeFilter", () => {
  it("returns dropped counts and their total for a well-formed blob", () => {
    const result = parseScopeFilter({
      scope_filter: { applied: true, dropped: { maven: 3, npm: 12 }, kept: 42 },
    });
    expect(result).toEqual({ dropped: { maven: 3, npm: 12 }, totalDropped: 15 });
  });

  it("returns null when metadata is undefined", () => {
    expect(parseScopeFilter(undefined)).toBeNull();
  });

  it("returns null when the scan carries no scope_filter key (pre-Phase-K)", () => {
    expect(parseScopeFilter({ seeded: true })).toBeNull();
  });

  it("returns null when nothing was dropped (filter ran, found nothing)", () => {
    expect(
      parseScopeFilter({ scope_filter: { applied: true, dropped: {}, kept: 10 } }),
    ).toBeNull();
  });

  it("ignores zero, negative and non-numeric counts", () => {
    expect(
      parseScopeFilter({
        scope_filter: { dropped: { maven: 0, npm: -2, weird: "5" } },
      }),
    ).toBeNull();
    expect(
      parseScopeFilter({
        scope_filter: { dropped: { maven: 2, weird: "5", broken: NaN } },
      }),
    ).toEqual({ dropped: { maven: 2 }, totalDropped: 2 });
  });

  it("tolerates hostile non-object shapes at every level", () => {
    expect(parseScopeFilter({ scope_filter: "not-an-object" })).toBeNull();
    expect(parseScopeFilter({ scope_filter: null })).toBeNull();
    expect(parseScopeFilter({ scope_filter: { dropped: [1, 2] } })).toBeNull();
    expect(parseScopeFilter({ scope_filter: { dropped: null } })).toBeNull();
  });
});
