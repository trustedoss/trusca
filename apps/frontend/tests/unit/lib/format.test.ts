/**
 * format: the number rules the screens did not share (B4).
 */
import { describe, expect, it } from "vitest";

import {
  ABSENT,
  formatNumber,
  formatPercent,
  formatSignedDelta,
  resolveLocale,
} from "@/lib/format";

describe("formatNumber", () => {
  it("groups a number past a thousand", () => {
    // The dashboard's open-vulnerability tile is portfolio-wide and rendered
    // "12480" in a slot styled `tabular-nums` for exactly this.
    expect(formatNumber(12480, "en")).toBe("12,480");
  });

  it("leaves a small number alone", () => {
    expect(formatNumber(7, "en")).toBe("7");
  });

  it("follows the locale it is given, not the browser's", () => {
    // de-DE groups with a full stop, so this fails if the locale is dropped.
    expect(formatNumber(12480, "de-DE")).toBe("12.480");
  });

  it.each([
    ["null", null],
    ["undefined", undefined],
    ["NaN", Number.NaN],
    ["Infinity", Number.POSITIVE_INFINITY],
  ])("renders the placeholder, not zero, for %s", (_label, value) => {
    // Not "0". The trend panel carries its own note that drawing zero for a
    // series it failed to load is the one answer it must never give, and a
    // shared helper that quietly does so is a trap for the next caller.
    expect(formatNumber(value, "en")).toBe(ABSENT);
  });

  it("falls back rather than throwing on a malformed locale tag", () => {
    expect(() => formatNumber(1234, "ko_KR")).not.toThrow();
    expect(formatNumber(1234, "ko_KR")).toContain("1");
  });
});

describe("formatSignedDelta", () => {
  it("puts the direction in the text, not only in the colour", () => {
    expect(formatSignedDelta(3, "en")).toBe("+3");
    expect(formatSignedDelta(-2, "en")).toBe("−2");
  });

  it("gives zero no sign at all", () => {
    // The trend card printed "+0 −0" on a quiet window, which reads as two
    // movements that cancelled rather than as nothing having happened.
    expect(formatSignedDelta(0, "en")).toBe("0");
  });

  it("groups the magnitude", () => {
    expect(formatSignedDelta(-12480, "en")).toBe("−12,480");
  });

  it("uses the minus sign, not a hyphen", () => {
    // U+2212. The hyphen is narrower and is not a minus.
    expect(formatSignedDelta(-1, "en")).toContain("−");
    expect(formatSignedDelta(-1, "en")).not.toContain("-");
  });

  it("renders an absent value as the placeholder, not as zero", () => {
    // "No movement" and "we do not know" are different answers.
    expect(formatSignedDelta(null, "en")).toBe(ABSENT);
  });

  it("treats negative zero as zero", () => {
    // Call sites negate a count to get the downward direction, and negating
    // zero in JavaScript gives -0.
    expect(formatSignedDelta(-0, "en")).toBe("0");
  });
});

describe("formatPercent", () => {
  it("renders whole percent by default", () => {
    expect(formatPercent(78.4, "en")).toBe("78%");
  });

  it("takes the digits it is asked for", () => {
    expect(formatPercent(78.4, "en", 1)).toBe("78.4%");
  });

  it("keeps a whole value whole", () => {
    expect(formatPercent(80, "en")).toBe("80%");
  });

  it("reads its input as 0..100, not 0..1", () => {
    // A percentage from the backend arrives as 78.4, not 0.784. Reading it
    // the other way would render "7,840%".
    expect(formatPercent(100, "en")).toBe("100%");
  });

  it("renders the placeholder for an absent value", () => {
    // Not "0%", which would claim a disk is empty when the reading failed.
    expect(formatPercent(null, "en")).toBe(ABSENT);
  });

  it("lets the locale place the percent sign", () => {
    // Turkish puts it in front. Appending "%" ourselves would be wrong there,
    // which is why the unit comes from Intl rather than from a template.
    expect(formatPercent(80, "tr")).toBe("%80");
  });
});

describe("resolveLocale", () => {
  it("prefers the resolved language", () => {
    expect(resolveLocale({ resolvedLanguage: "ko", language: "ko-KR" })).toBe(
      "ko",
    );
  });

  it("falls back to the raw tag when nothing resolved", () => {
    // Half the call sites read `resolvedLanguage` alone; where that came back
    // undefined the formatter fell silently to the browser's locale, which is
    // the defect this module exists to remove.
    expect(resolveLocale({ language: "ko-KR" })).toBe("ko-KR");
  });
});
