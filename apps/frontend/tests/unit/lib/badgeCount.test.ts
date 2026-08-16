/**
 * formatBadge - the shared cap for the bell and the sidebar badges.
 *
 * Moved out of HeaderBell.test.tsx in C1, when the sidebar started using it.
 */
import { describe, expect, it } from "vitest";

import { formatBadge } from "@/lib/badgeCount";

describe("formatBadge", () => {
  it("returns empty string for 0", () => {
    expect(formatBadge(0)).toBe("");
  });

  it("returns empty string for negative counts (defensive)", () => {
    expect(formatBadge(-5)).toBe("");
  });

  it("formats a single-digit count as itself", () => {
    expect(formatBadge(3)).toBe("3");
  });

  it("formats 99 as '99'", () => {
    expect(formatBadge(99)).toBe("99");
  });

  it("caps anything over 99 at '99+'", () => {
    expect(formatBadge(100)).toBe("99+");
    expect(formatBadge(250)).toBe("99+");
  });
});
