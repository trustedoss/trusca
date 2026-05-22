/**
 * sourceHighlight — unit tests (G3.3).
 *
 * The per-line license-highlight projection is the load-bearing logic of the
 * source viewer, so it is tested as a pure function (no DOM). Covers line
 * splitting, range coverage (inclusive, multi-line), overlap dedupe + score
 * ordering, out-of-range clamping, binary/null content, and tooltip text.
 */
import { describe, expect, it } from "vitest";

import type { LicenseMatch } from "@/features/projects/api/sourceTreeApi";
import {
  buildSourceLines,
  formatMatchTooltip,
  splitLines,
} from "@/features/projects/lib/sourceHighlight";

function match(
  spdx_id: string,
  start_line: number,
  end_line: number,
  score: number | null = 99,
): LicenseMatch {
  return { spdx_id, start_line, end_line, score };
}

describe("splitLines", () => {
  it("splits on \\n and drops a single trailing-newline phantom line", () => {
    expect(splitLines("a\nb\nc\n")).toEqual(["a", "b", "c"]);
  });

  it("normalises CRLF / CR so line indices match the backend", () => {
    expect(splitLines("a\r\nb\rc")).toEqual(["a", "b", "c"]);
  });

  it("returns [] for null (binary) content and [''] for an empty string", () => {
    expect(splitLines(null)).toEqual([]);
    expect(splitLines(undefined)).toEqual([]);
    expect(splitLines("")).toEqual([""]);
  });

  it("keeps interior blank lines", () => {
    expect(splitLines("a\n\nb")).toEqual(["a", "", "b"]);
  });
});

describe("buildSourceLines", () => {
  it("numbers lines 1-based and marks only covered lines as highlighted", () => {
    const lines = buildSourceLines("l1\nl2\nl3\nl4", [match("MIT", 2, 3)]);
    expect(lines.map((l) => l.number)).toEqual([1, 2, 3, 4]);
    expect(lines.map((l) => l.matches.length > 0)).toEqual([
      false,
      true,
      true,
      false,
    ]);
    expect(lines[1].matches[0].spdx_id).toBe("MIT");
  });

  it("accumulates overlapping matches on a line, deduped by spdx with the best score", () => {
    const lines = buildSourceLines("a\nb\nc", [
      match("MIT", 1, 2, 80),
      match("MIT", 2, 3, 95), // same license, higher score on line 2
      match("Apache-2.0", 2, 2, 50),
    ]);
    const line2 = lines[1];
    // Two distinct licenses on line 2.
    expect(line2.matches.map((m) => m.spdx_id)).toEqual(["MIT", "Apache-2.0"]);
    // MIT kept the higher (95) score, not the 80 from the first match.
    expect(line2.matches[0]).toEqual({ spdx_id: "MIT", score: 95 });
    // Ordered by descending score (95 before 50).
    expect(line2.matches[1].score).toBe(50);
  });

  it("clamps a match that runs past EOF instead of throwing", () => {
    const lines = buildSourceLines("only-one-line", [match("MIT", 1, 999)]);
    expect(lines).toHaveLength(1);
    expect(lines[0].matches[0].spdx_id).toBe("MIT");
  });

  it("tolerates a reversed range (start > end)", () => {
    const lines = buildSourceLines("a\nb\nc", [match("MIT", 3, 1)]);
    expect(lines.every((l) => l.matches.length > 0)).toBe(true);
  });

  it("returns [] for binary (null) content even with matches present", () => {
    expect(buildSourceLines(null, [match("MIT", 1, 1)])).toEqual([]);
  });

  it("returns lines with no matches when the match list is empty", () => {
    const lines = buildSourceLines("a\nb", []);
    expect(lines.every((l) => l.matches.length === 0)).toBe(true);
  });
});

describe("formatMatchTooltip", () => {
  it("joins licenses with a middle dot and rounds scores to a percentage", () => {
    expect(
      formatMatchTooltip([
        { spdx_id: "MIT", score: 99.5 },
        { spdx_id: "Apache-2.0", score: 50.2 },
      ]),
    ).toBe("MIT 100% · Apache-2.0 50%");
  });

  it("omits the percentage for a null score", () => {
    expect(formatMatchTooltip([{ spdx_id: "MIT", score: null }])).toBe("MIT");
  });

  it("returns an empty string for no matches", () => {
    expect(formatMatchTooltip([])).toBe("");
  });
});
