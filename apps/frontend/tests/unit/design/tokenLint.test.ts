/**
 * G0-1 — tests for the design-token ratchet lint itself.
 *
 * A gate nobody has tested is a gate nobody can trust: if `scan()` silently
 * stopped matching, the baseline would keep passing and the ratchet would
 * report "OK" forever while debt piled up underneath. So this suite drives
 * the linter over fixture trees and asserts both directions — it catches
 * what it should, and it stays quiet on what it must not flag (comments
 * citing issue numbers, vendor brand marks, token definition files).
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { diff, scan, stripComments } from "../../../scripts/token-lint.mjs";

let root: string;

/** Write `src/<rel>` inside a throwaway frontend root and return its path. */
function write(rel: string, content: string): void {
  const full = path.join(root, "src", rel);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, content);
}

function run() {
  return scan(path.join(root, "src"), root);
}

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "token-lint-"));
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe("stripComments", () => {
  it("blanks line and block comments without moving line numbers", () => {
    const source = [
      "const a = 1; // bg-amber-50",
      "/* multi",
      "   #43e cited here",
      "*/",
      "const b = 2;",
    ].join("\n");

    const stripped = stripComments(source);

    expect(stripped.split("\n")).toHaveLength(5);
    expect(stripped).not.toContain("bg-amber-50");
    expect(stripped).not.toContain("#43e");
    expect(stripped.split("\n")[4]).toBe("const b = 2;");
  });
});

describe("scan", () => {
  it("flags palette classes and raw hex in real code", () => {
    write(
      "features/Thing.tsx",
      [
        'const cls = "border-amber-300 bg-amber-50 text-amber-900";',
        'const dot = { color: "#dc2626" };',
      ].join("\n"),
    );

    const { counts, findings } = run();

    expect(counts["src/features/Thing.tsx"]).toBe(4);
    expect(findings.map((f) => f.text)).toEqual([
      "border-amber-300",
      "bg-amber-50",
      "text-amber-900",
      "#dc2626",
    ]);
    expect(findings[0].line).toBe(1);
    expect(findings[3].line).toBe(2);
  });

  it("reports the line the violation is actually on", () => {
    write(
      "features/Later.tsx",
      [
        "/**",
        " * Header citing W6-#43e and a colour we used to use: #0f172a.",
        " */",
        "export const x = 1;",
        'export const cls = "text-emerald-700";',
      ].join("\n"),
    );

    const { findings } = run();

    expect(findings).toHaveLength(1);
    expect(findings[0]).toMatchObject({ line: 5, text: "text-emerald-700" });
  });

  it("does not flag issue numbers or colour history in comments", () => {
    write(
      "features/Commented.tsx",
      [
        "// W6-#43e reverted the #0f172a navy — see bg-amber-50 note below.",
        "/* chore #365: the old text-slate-600 fix lives in badge.tsx */",
        "export const clean = true;",
      ].join("\n"),
    );

    expect(run().findings).toEqual([]);
  });

  it("does not flag the design system's own semantic scales", () => {
    write(
      "features/Tokened.tsx",
      [
        'const ok = "bg-status-warning-subtle border-status-warning-border";',
        'const also = "text-risk-medium-foreground bg-risk-medium/15";',
        'const shadcn = "bg-muted text-muted-foreground border-destructive";',
      ].join("\n"),
    );

    expect(run().findings).toEqual([]);
  });

  it("exempts token definitions, brand marks and tests", () => {
    write("index.css", "--risk-critical: #dc2626;");
    write("components/BrandMark.tsx", 'const tile = "#0f172a";');
    write("components/ProviderIcon.tsx", 'const google = "#4285F4";');
    write("features/Thing.test.tsx", 'expect(c).toBe("#dc2626");');

    expect(run().findings).toEqual([]);
  });

  it("skips files that carry no colour (e.g. plain JSON, images)", () => {
    write("data/fixture.json", '{"color": "#dc2626"}');

    expect(run().findings).toEqual([]);
  });
});

describe("diff — the ratchet", () => {
  const baseline = { "src/a.tsx": 3, "src/b.tsx": 1 };

  it("passes when every file sits exactly on its budget", () => {
    const result = diff({ "src/a.tsx": 3, "src/b.tsx": 1 }, baseline);

    expect(result.ok).toBe(true);
    expect(result.total).toBe(4);
    expect(result.baselineTotal).toBe(4);
  });

  it("fails on a violation in a file with no budget", () => {
    const result = diff({ ...baseline, "src/new.tsx": 1 }, baseline);

    expect(result.ok).toBe(false);
    expect(result.added).toEqual([{ file: "src/new.tsx", count: 1 }]);
  });

  it("fails when a file's count grows", () => {
    const result = diff({ "src/a.tsx": 4, "src/b.tsx": 1 }, baseline);

    expect(result.ok).toBe(false);
    expect(result.grew).toEqual([{ file: "src/a.tsx", count: 4, budget: 3 }]);
  });

  it("fails when a file's count drops, so the gain gets committed", () => {
    // This is what makes it a ratchet and not a cap: paid-down debt must be
    // recorded, otherwise the budget stays available for someone to re-spend.
    const result = diff({ "src/a.tsx": 1, "src/b.tsx": 1 }, baseline);

    expect(result.ok).toBe(false);
    expect(result.shrank).toEqual([{ file: "src/a.tsx", count: 1, budget: 3 }]);
  });

  it("fails when a baselined file is cleaned out entirely", () => {
    const result = diff({ "src/a.tsx": 3 }, baseline);

    expect(result.ok).toBe(false);
    expect(result.shrank).toEqual([{ file: "src/b.tsx", count: 0, budget: 1 }]);
  });
});
