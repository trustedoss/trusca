/**
 * searchParamsToggle.ts — unit tests (W9-#57).
 *
 * The helper underpins chart-segment "click again to clear" behaviour across
 * five distinct screens (Dashboard, Project List, Overview, Vulnerabilities,
 * Components / Licenses). The toggle rule is small but easy to get wrong, so
 * we lock it down here once instead of duplicating the assertion in every
 * screen-level test.
 */
import { describe, expect, it } from "vitest";

import {
  toggleNullable,
  toggleSearchParam,
  toggleSingleValue,
} from "@/lib/searchParamsToggle";

describe("toggleSearchParam", () => {
  it("sets the key when it is absent", () => {
    const next = toggleSearchParam(
      new URLSearchParams(),
      "severity",
      "critical",
    );
    expect(next.getAll("severity")).toEqual(["critical"]);
  });

  it("clears the key when the only active value matches", () => {
    const next = toggleSearchParam(
      new URLSearchParams("severity=critical"),
      "severity",
      "critical",
    );
    expect(next.getAll("severity")).toEqual([]);
    expect(next.has("severity")).toBe(false);
  });

  it("replaces the key when a different single value is active", () => {
    const next = toggleSearchParam(
      new URLSearchParams("severity=high"),
      "severity",
      "critical",
    );
    expect(next.getAll("severity")).toEqual(["critical"]);
  });

  it("collapses a multi-value selection onto the clicked value", () => {
    // A multi-valued facet (`?severity=high&severity=medium`) gets replaced
    // by `?severity=critical` when the chart click lands on `critical`.
    // The chart click is always a single-segment focus, so we collapse rather
    // than splice — keeps the rule predictable.
    const next = toggleSearchParam(
      new URLSearchParams("severity=high&severity=medium"),
      "severity",
      "critical",
    );
    expect(next.getAll("severity")).toEqual(["critical"]);
  });

  it("collapses a multi-value selection that contains the clicked value", () => {
    // Same rule even when the clicked value is one of the active values: the
    // single chart click is treated as a focus, not a per-value toggle.
    const next = toggleSearchParam(
      new URLSearchParams("severity=high&severity=critical"),
      "severity",
      "critical",
    );
    expect(next.getAll("severity")).toEqual(["critical"]);
  });

  it("preserves unrelated keys when toggling", () => {
    const next = toggleSearchParam(
      new URLSearchParams("severity=critical&q=lodash&page=2"),
      "severity",
      "critical",
    );
    expect(next.has("severity")).toBe(false);
    expect(next.get("q")).toBe("lodash");
    expect(next.get("page")).toBe("2");
  });

  it("applies `also` mutations alongside the toggle", () => {
    // Used by OverviewTab's chart deep-link: toggle the filter AND switch tab.
    const next = toggleSearchParam(
      new URLSearchParams(),
      "severity",
      "critical",
      { also: { tab: "vulnerabilities" } },
    );
    expect(next.get("tab")).toBe("vulnerabilities");
    expect(next.getAll("severity")).toEqual(["critical"]);
  });

  it("deletes `also` keys when their value is null or empty", () => {
    const next = toggleSearchParam(
      new URLSearchParams("cview=licenses&extra=x"),
      "severity",
      "critical",
      { also: { cview: null, extra: "" } },
    );
    expect(next.has("cview")).toBe(false);
    expect(next.has("extra")).toBe(false);
  });

  it("does not mutate the input params instance", () => {
    const input = new URLSearchParams("severity=critical");
    const next = toggleSearchParam(input, "severity", "critical");
    expect(input.get("severity")).toBe("critical");
    expect(next.has("severity")).toBe(false);
  });
});

describe("toggleSingleValue", () => {
  it("returns [value] when the array is empty", () => {
    expect(toggleSingleValue<string>([], "critical")).toEqual(["critical"]);
  });

  it("returns [] when the array contains only the clicked value", () => {
    expect(toggleSingleValue(["critical"], "critical")).toEqual([]);
  });

  it("returns [value] when the array holds a different single value", () => {
    expect(toggleSingleValue(["high"], "critical")).toEqual(["critical"]);
  });

  it("collapses multi-selection to the clicked value", () => {
    expect(
      toggleSingleValue(["high", "medium"], "critical"),
    ).toEqual(["critical"]);
  });

  it("collapses multi-selection that contains the clicked value", () => {
    expect(
      toggleSingleValue(["high", "critical"], "critical"),
    ).toEqual(["critical"]);
  });
});

describe("toggleNullable", () => {
  it("returns the new value when current is null", () => {
    expect(toggleNullable<string>(null, "critical")).toBe("critical");
  });

  it("returns null when the new value matches current", () => {
    expect(toggleNullable("critical", "critical")).toBeNull();
  });

  it("returns the new value when it differs from current", () => {
    expect(toggleNullable("high", "critical")).toBe("critical");
  });
});
