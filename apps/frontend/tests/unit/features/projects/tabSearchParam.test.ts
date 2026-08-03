import { describe, expect, it } from "vitest";

import {
  COMPONENTS_SEARCH_PARAM,
  LEGACY_SEARCH_PARAM,
  LICENSES_SEARCH_PARAM,
  OBLIGATIONS_SEARCH_PARAM,
  VULNERABILITIES_SEARCH_PARAM,
  readTabSearchParam,
  writeTabSearchParam,
} from "@/features/projects/components/tabSearchParam";

/**
 * S1-5 — per-tab search params.
 *
 * The defect these guard: all four detail tabs wrote the same `?search=` key,
 * so a term typed on Components silently filtered the Vulnerabilities table
 * one click later (an empty list that read as "no findings"). The fix gives
 * each tab its own key while keeping pre-existing deep links working.
 */

describe("tab search params", () => {
  it("gives every tab a distinct key", () => {
    const keys = [
      COMPONENTS_SEARCH_PARAM,
      VULNERABILITIES_SEARCH_PARAM,
      LICENSES_SEARCH_PARAM,
      OBLIGATIONS_SEARCH_PARAM,
    ];
    expect(new Set(keys).size).toBe(keys.length);
    // None of them may collide with the shared key they replace, or the bug
    // would survive the rename.
    expect(keys).not.toContain(LEGACY_SEARCH_PARAM);
  });

  describe("readTabSearchParam", () => {
    it("prefers the tab's own key", () => {
      const params = new URLSearchParams({
        [COMPONENTS_SEARCH_PARAM]: "lodash",
        [LEGACY_SEARCH_PARAM]: "stale",
      });
      expect(readTabSearchParam(params, COMPONENTS_SEARCH_PARAM)).toBe("lodash");
    });

    it("falls back to the legacy shared key so old deep links still filter", () => {
      const params = new URLSearchParams({ [LEGACY_SEARCH_PARAM]: "lodash" });
      expect(readTabSearchParam(params, COMPONENTS_SEARCH_PARAM)).toBe("lodash");
    });

    it("returns an empty string when neither key is present", () => {
      expect(readTabSearchParam(new URLSearchParams(), COMPONENTS_SEARCH_PARAM)).toBe("");
    });

    it("does not read a sibling tab's term", () => {
      const params = new URLSearchParams({
        [COMPONENTS_SEARCH_PARAM]: "lodash",
      });
      expect(readTabSearchParam(params, VULNERABILITIES_SEARCH_PARAM)).toBe("");
    });
  });

  describe("writeTabSearchParam", () => {
    it("writes the tab's key and retires the legacy one", () => {
      const params = new URLSearchParams({ [LEGACY_SEARCH_PARAM]: "lodash" });
      writeTabSearchParam(params, COMPONENTS_SEARCH_PARAM, "lodash");
      expect(params.get(COMPONENTS_SEARCH_PARAM)).toBe("lodash");
      expect(params.has(LEGACY_SEARCH_PARAM)).toBe(false);
    });

    it("deletes the tab's key when the term is cleared", () => {
      const params = new URLSearchParams({ [COMPONENTS_SEARCH_PARAM]: "lodash" });
      writeTabSearchParam(params, COMPONENTS_SEARCH_PARAM, "");
      expect(params.has(COMPONENTS_SEARCH_PARAM)).toBe(false);
    });

    it("leaves a sibling tab's term untouched", () => {
      const params = new URLSearchParams({
        [VULNERABILITIES_SEARCH_PARAM]: "CVE-2021-1234",
      });
      writeTabSearchParam(params, COMPONENTS_SEARCH_PARAM, "lodash");
      expect(params.get(VULNERABILITIES_SEARCH_PARAM)).toBe("CVE-2021-1234");
      expect(params.get(COMPONENTS_SEARCH_PARAM)).toBe("lodash");
    });

    it("preserves unrelated params", () => {
      const params = new URLSearchParams({ tab: "components", severity: "critical" });
      writeTabSearchParam(params, COMPONENTS_SEARCH_PARAM, "lodash");
      expect(params.get("tab")).toBe("components");
      expect(params.get("severity")).toBe("critical");
    });

    it("round-trips a term containing URL-significant characters", () => {
      const params = new URLSearchParams();
      const term = "pkg:npm/@scope/name&x=1";
      writeTabSearchParam(params, COMPONENTS_SEARCH_PARAM, term);
      const reparsed = new URLSearchParams(params.toString());
      expect(readTabSearchParam(reparsed, COMPONENTS_SEARCH_PARAM)).toBe(term);
    });
  });

  it("migrates a legacy link in one read-then-write cycle", () => {
    // The exact sequence a mounted tab performs: hydrate from the URL, then
    // sync its state back. After one cycle the ambiguous key is gone, so no
    // sibling tab can inherit it.
    const params = new URLSearchParams({
      tab: "components",
      [LEGACY_SEARCH_PARAM]: "lodash",
    });
    const hydrated = readTabSearchParam(params, COMPONENTS_SEARCH_PARAM);
    writeTabSearchParam(params, COMPONENTS_SEARCH_PARAM, hydrated);

    expect(params.get(COMPONENTS_SEARCH_PARAM)).toBe("lodash");
    expect(params.has(LEGACY_SEARCH_PARAM)).toBe(false);
    expect(readTabSearchParam(params, VULNERABILITIES_SEARCH_PARAM)).toBe("");
  });
});
