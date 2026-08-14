/**
 * Tests for the error-copy ratchet itself.
 *
 * A ratchet nobody tested is a ratchet nobody can trust: if `scan()` stopped
 * matching, the baseline would keep passing and the debt would grow under a
 * green check. So this drives the linter over fixture trees and asserts both
 * directions — it catches the bypass, and it stays quiet on the things that
 * merely look like one (a domain payload's `detail`, a mention in a comment,
 * the helper that is the sanctioned path).
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  diff,
  scan,
  stripComments,
} from "../../../scripts/problem-detail-lint.mjs";

let root: string;

function write(rel: string, content: string): void {
  const full = path.join(root, "src", rel);
  fs.mkdirSync(path.dirname(full), { recursive: true });
  fs.writeFileSync(full, content);
}

function run() {
  return scan(path.join(root, "src"), root);
}

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), "problem-detail-lint-"));
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

describe("stripComments", () => {
  it("blanks comments without moving line numbers", () => {
    const source = [
      "const a = 1; // err.detail mentioned in prose",
      "/* block",
      "   error.detail here too",
      "*/",
      "const b = 2;",
    ].join("\n");

    const stripped = stripComments(source);

    expect(stripped.split("\n")).toHaveLength(5);
    expect(stripped).not.toContain("err.detail");
    expect(stripped).not.toContain("error.detail");
    expect(stripped.split("\n")[4]).toBe("const b = 2;");
  });

  it("leaves a URL's double slash alone", () => {
    // `https://` must not read as a line comment, or everything after a URL
    // on that line stops being scanned.
    const stripped = stripComments('const u = "https://x/y"; err.detail;');
    expect(stripped).toContain("err.detail");
  });
});

describe("scan", () => {
  it("flags an error's detail read straight into the UI", () => {
    write(
      "features/x/Panel.tsx",
      [
        "export function Panel({ err }: { err: unknown }) {",
        '  const text = err instanceof ProblemError ? err.detail : t("x");',
        "  return <span>{text}</span>;",
        "}",
      ].join("\n"),
    );

    const { counts, findings } = run();

    expect(counts["src/features/x/Panel.tsx"]).toBe(1);
    expect(findings[0]).toMatchObject({ line: 2 });
  });

  it("flags the receivers this codebase actually uses", () => {
    write(
      "features/x/Many.tsx",
      [
        "const a = error.detail;",
        "const b = mutationError.detail;",
        "const c = createErr.detail;",
        "const d = query.error.detail;",
        "const e = problem.detail;",
      ].join("\n"),
    );

    expect(run().counts["src/features/x/Many.tsx"]).toBe(5);
  });

  it("flags title too, not just detail", () => {
    // `title` is equally English, and watching only `detail` would leave the
    // door open next to the one being closed.
    write("features/x/Title.tsx", "const a = err.title;");

    expect(run().counts["src/features/x/Title.tsx"]).toBe(1);
  });

  it("flags optional and non-null access", () => {
    // One character away from the plain form, so these slip through without
    // anyone meaning to evade the gate.
    write(
      "features/x/Optional.tsx",
      ["const a = err?.detail;", "const b = error!.title;"].join("\n"),
    );

    expect(run().counts["src/features/x/Optional.tsx"]).toBe(2);
  });

  it("flags a detail read through a cast", () => {
    // A cast is what someone reaches for when the plain form is flagged, and
    // the receiver-name rule cannot see past the closing paren.
    write(
      "features/x/Cast.tsx",
      [
        "const a = (err as any).detail;",
        "const b = (caught as ProblemError).detail;",
        "const c = (thing as unknown as ProblemError).detail;",
      ].join("\n"),
    );

    expect(run().counts["src/features/x/Cast.tsx"]).toBe(3);
  });

  it("stays quiet on a domain payload that happens to have a detail", () => {
    // The health panel's component detail and a bulk result's per-item reason
    // are payload fields, not RFC 7807. Flagging them would train people to
    // rename their data around the linter.
    write(
      "features/x/Health.tsx",
      [
        "const a = component.detail;",
        "const b = failure.detail;",
        "const c = row.detail;",
      ].join("\n"),
    );

    expect(run().counts["src/features/x/Health.tsx"]).toBeUndefined();
  });

  it("stays quiet on the sanctioned helpers", () => {
    write("lib/problemMessage.ts", "return err.detail;");
    write("features/projects/lib/projectErrorMessage.ts", "return err.detail;");

    expect(Object.keys(run().counts)).toEqual([]);
  });

  it("ignores non-source files", () => {
    write("features/x/notes.md", "err.detail");
    expect(Object.keys(run().counts)).toEqual([]);
  });
});

describe("diff", () => {
  it("fails a new file and a grown file", () => {
    const result = diff(
      { "src/a.tsx": 1, "src/b.tsx": 3 },
      { "src/b.tsx": 2 },
    );

    expect(result.ok).toBe(false);
    expect(result.added).toEqual([{ file: "src/a.tsx", count: 1 }]);
    expect(result.grew).toEqual([
      { file: "src/b.tsx", count: 3, budget: 2 },
    ]);
  });

  it("fails a shrunk file too, so the budget cannot be re-spent", () => {
    const result = diff({ "src/b.tsx": 1 }, { "src/b.tsx": 2 });

    expect(result.ok).toBe(false);
    expect(result.shrank).toEqual([
      { file: "src/b.tsx", count: 1, budget: 2 },
    ]);
  });

  it("fails a file that dropped to zero without being re-recorded", () => {
    const result = diff({}, { "src/b.tsx": 2 });

    expect(result.ok).toBe(false);
    expect(result.shrank).toEqual([
      { file: "src/b.tsx", count: 0, budget: 2 },
    ]);
  });

  it("passes when the counts match", () => {
    const result = diff({ "src/b.tsx": 2 }, { "src/b.tsx": 2 });

    expect(result.ok).toBe(true);
    expect(result.total).toBe(2);
    expect(result.baselineTotal).toBe(2);
  });
});
