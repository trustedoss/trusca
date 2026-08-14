/**
 * EPSS formatting helpers — unit tests (v2.1).
 *
 * Locks the display contract reported back to the team: score becomes a
 * one-decimal percentage, percentile becomes a translation key naming its top
 * band, and null / out-of-range becomes null so callers render the dash
 * placeholder rather than "0%".
 */
import { describe, expect, it } from "vitest";

import {
  EPSS_EMPTY,
  epssPercentileLabel,
  formatEpssScore,
} from "@/features/projects/lib/epss";
import i18n from "@/lib/i18n";

describe("formatEpssScore", () => {
  it("formats a probability as a one-decimal percentage", () => {
    expect(formatEpssScore(0.973)).toBe("97.3%");
    expect(formatEpssScore(0.5)).toBe("50.0%");
    expect(formatEpssScore(0.00044)).toBe("0.0%");
  });

  it("renders the boundaries 0 and 1", () => {
    expect(formatEpssScore(0)).toBe("0.0%");
    expect(formatEpssScore(1)).toBe("100.0%");
  });

  it("returns null for missing values", () => {
    expect(formatEpssScore(null)).toBeNull();
    expect(formatEpssScore(undefined)).toBeNull();
  });

  it("returns null for out-of-range / non-finite values", () => {
    expect(formatEpssScore(-0.1)).toBeNull();
    expect(formatEpssScore(1.5)).toBeNull();
    expect(formatEpssScore(Number.NaN)).toBeNull();
    expect(formatEpssScore(Number.POSITIVE_INFINITY)).toBeNull();
  });
});

const TOP = "vulnerabilities.epss.top_percentile";
const TOP_SUB1 = "vulnerabilities.epss.top_percentile_sub1";

describe("epssPercentileLabel", () => {
  it("describes a rank as the Top N% key and its value", () => {
    expect(epssPercentileLabel(0.91)).toEqual({ key: TOP, params: { value: 9 } });
    expect(epssPercentileLabel(0.5)).toEqual({ key: TOP, params: { value: 50 } });
    expect(epssPercentileLabel(0)).toEqual({ key: TOP, params: { value: 100 } });
  });

  it("gives a sub-percent rank its own key instead of rounding to zero", () => {
    expect(epssPercentileLabel(0.999)).toEqual({ key: TOP_SUB1, params: {} });
  });

  it("still says top 0% at percentile 1, where it is literally true", () => {
    expect(epssPercentileLabel(1)).toEqual({ key: TOP, params: { value: 0 } });
  });

  it("returns null for missing / out-of-range values", () => {
    expect(epssPercentileLabel(null)).toBeNull();
    expect(epssPercentileLabel(undefined)).toBeNull();
    expect(epssPercentileLabel(-0.2)).toBeNull();
    expect(epssPercentileLabel(2)).toBeNull();
    expect(epssPercentileLabel(Number.NaN)).toBeNull();
  });

  it("names keys that both locales actually ship", () => {
    // The keys are built at runtime, so the i18n drift gate cannot see them.
    for (const locale of ["en", "ko"]) {
      for (const key of [TOP, TOP_SUB1]) {
        const text = i18n.getFixedT(locale, "project_detail")(key);
        expect(text, `${locale}:project_detail:${key} is missing`).not.toBe(key);
      }
    }
  });
});

describe("EPSS_EMPTY", () => {
  it("is the em-dash placeholder", () => {
    expect(EPSS_EMPTY).toBe("—");
  });
});
