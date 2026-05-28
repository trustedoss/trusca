/**
 * EPSS formatting helpers — unit tests (v2.1).
 *
 * Locks the display contract reported back to the team: score → one-decimal
 * percentage, percentile → "Top N%", and null/out-of-range → null (so callers
 * render the em-dash placeholder rather than "0%").
 */
import { describe, expect, it } from "vitest";

import {
  EPSS_EMPTY,
  formatEpssPercentile,
  formatEpssScore,
} from "@/features/projects/lib/epss";

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

describe("formatEpssPercentile", () => {
  it("renders a rank as Top N%", () => {
    expect(formatEpssPercentile(0.91)).toBe("Top 9%");
    expect(formatEpssPercentile(0.5)).toBe("Top 50%");
    expect(formatEpssPercentile(0)).toBe("Top 100%");
  });

  it("clamps a near-top rank to Top <1% instead of Top 0%", () => {
    expect(formatEpssPercentile(0.999)).toBe("Top <1%");
  });

  it("renders exactly Top 0% only at percentile 1 (the very top)", () => {
    expect(formatEpssPercentile(1)).toBe("Top 0%");
  });

  it("returns null for missing / out-of-range values", () => {
    expect(formatEpssPercentile(null)).toBeNull();
    expect(formatEpssPercentile(undefined)).toBeNull();
    expect(formatEpssPercentile(-0.2)).toBeNull();
    expect(formatEpssPercentile(2)).toBeNull();
    expect(formatEpssPercentile(Number.NaN)).toBeNull();
  });
});

describe("EPSS_EMPTY", () => {
  it("is the em-dash placeholder", () => {
    expect(EPSS_EMPTY).toBe("—");
  });
});
