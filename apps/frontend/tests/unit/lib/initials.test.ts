/**
 * deriveInitials — M-17 header avatar monogram derivation.
 */
import { describe, expect, it } from "vitest";

import { deriveInitials } from "@/lib/initials";

describe("deriveInitials", () => {
  it("derives two letters from a two-word full name", () => {
    expect(deriveInitials("Haksung Jang")).toBe("HJ");
  });

  it("uses first and last word for 3+ word names", () => {
    expect(deriveInitials("Anna Maria Park")).toBe("AP");
  });

  it("derives a single letter from a one-word name", () => {
    expect(deriveInitials("Haksung")).toBe("H");
  });

  it("falls back to the email local part's first character", () => {
    expect(deriveInitials("dev@x.com")).toBe("D");
  });

  it("treats a dotted email local part as a single word", () => {
    expect(deriveInitials("first.last@example.com")).toBe("F");
  });

  it("uppercases lowercase input and trims whitespace", () => {
    expect(deriveInitials("  alice   smith  ")).toBe("AS");
  });

  it("returns an empty string for blank input (caller falls back to icon)", () => {
    expect(deriveInitials("")).toBe("");
    expect(deriveInitials("   ")).toBe("");
  });
});
