/**
 * Type floor: no text below 12 px, and no all-caps tracked labels.
 *
 * Primer's smallest body size is 12 px, and its table headers and subheads
 * are sentence case, semibold and muted. The portal had drifted from both:
 * about a hundred `text-[9px]` / `text-[10px]` / `text-[11px]` call sites, and
 * overlines, KPI labels and column headers drawn as `uppercase tracking-wide`.
 * The small sizes fall under the readable floor, and the uppercase styling
 * split the hierarchy by language because Korean has no case: an English
 * label read as a heading while the same Korean label read as body text.
 *
 * Neither fails anywhere else. The arbitrary sizes are valid Tailwind and
 * `uppercase` is a plain utility, so this guard parses every source file and
 * checks the strings that end up as classes or style values.
 *
 * Size check (all of src/, .ts, .tsx and .css):
 *   - Tailwind arbitrary sizes `text-[Npx]` / `text-[Nrem]` under 12 px
 *   - `fontSize` / `font-size` style values under 12 px (objects, JSX attributes)
 *   - CSS `font-size` declarations under 12 px
 *
 * Case check (.ts and .tsx): the Tailwind `uppercase` utility in any string
 * literal. Text that is uppercase because it is data (CVE ids, SPDX ids, the
 * literal "VEX" marker, NOTICE) is written in caps in the data or the
 * translation and needs no class. Running `uppercase` over data is wrong in
 * the other direction too: SPDX ids are case-sensitive (`Apache-2.0`).
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");
const SRC_ROOT = path.join(FRONTEND_ROOT, "src");

const FLOOR_PX = 12;
const ROOT_PX = 16;

/**
 * Sizes under the floor that are deliberate. Keyed by file, value is the
 * number of hits allowed. Keep the reason next to each entry.
 */
const SIZE_EXCEPTIONS: Record<string, number> = {
  // Cytoscape canvas label. It is a model-space size that the graph scales
  // with zoom (and hides under `min-zoomed-font-size`), not a rendered CSS
  // size, and the dagre layout spaces nodes for a label of this width.
  "src/features/projects/components/DependencyGraph.tsx": 1,
};

/**
 * Files allowed to keep uppercase labels, with an exact count. Empty: every
 * label is sentence case. A new entry needs a reason (data that must render
 * in caps) and the count is checked exactly, so it cannot grow unnoticed.
 */
const UPPERCASE_BASELINE: Record<string, number> = {};

interface Hit {
  line: number;
  text: string;
}

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) sourceFiles(full, out);
    else if (/\.(tsx?|css)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

/** Pixel value of a CSS length, or null when it is not px / rem / em. */
function toPx(value: string): number | null {
  const m = /^\s*(\d*\.?\d+)\s*(px|rem|em)?\s*$/.exec(value);
  if (!m) return null;
  const n = Number(m[1]);
  if (m[2] === "rem" || m[2] === "em") return n * ROOT_PX;
  return n;
}

const ARBITRARY_TEXT = /(?:^|[\s"'`:])text-\[(\d*\.?\d+(?:px|rem|em))\]/g;

function arbitraryUnderFloor(text: string): string[] {
  const found: string[] = [];
  for (const m of text.matchAll(ARBITRARY_TEXT)) {
    const px = toPx(m[1]);
    if (px !== null && px < FLOOR_PX) found.push(`text-[${m[1]}]`);
  }
  return found;
}

function isFontSizeName(name: string): boolean {
  return name === "fontSize" || name === "font-size";
}

function propName(name: ts.PropertyName | ts.JsxAttributeName, sf: ts.SourceFile): string {
  if (ts.isIdentifier(name) || ts.isStringLiteral(name)) return name.text;
  return name.getText(sf);
}

function literalUnderFloor(init: ts.Node): boolean {
  if (ts.isNumericLiteral(init)) return Number(init.text) < FLOOR_PX;
  if (ts.isStringLiteral(init) || ts.isNoSubstitutionTemplateLiteral(init)) {
    const px = toPx(init.text);
    return px !== null && px < FLOOR_PX;
  }
  if (ts.isJsxExpression(init) && init.expression) return literalUnderFloor(init.expression);
  return false;
}

/** Reports every font size under 12 px in a .ts / .tsx source. */
function findSmallSizes(fileName: string, source: string): Hit[] {
  if (fileName.endsWith(".css")) {
    const hits: Hit[] = [];
    source.split("\n").forEach((line, i) => {
      for (const m of line.matchAll(/font-size\s*:\s*([^;}]+)/g)) {
        const px = toPx(m[1]);
        if (px !== null && px < FLOOR_PX) hits.push({ line: i + 1, text: m[0].trim() });
      }
      for (const t of arbitraryUnderFloor(line)) hits.push({ line: i + 1, text: t });
    });
    return hits;
  }
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const hits: Hit[] = [];
  const at = (node: ts.Node) => sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
  const visit = (node: ts.Node): void => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      for (const t of arbitraryUnderFloor(node.text)) hits.push({ line: at(node), text: t });
    } else if (ts.isJsxText(node)) {
      // JSX text is copy, not classes.
    } else if (
      ts.isPropertyAssignment(node) &&
      isFontSizeName(propName(node.name, sf)) &&
      literalUnderFloor(node.initializer)
    ) {
      hits.push({ line: at(node), text: node.getText(sf) });
    } else if (
      ts.isJsxAttribute(node) &&
      isFontSizeName(propName(node.name, sf)) &&
      node.initializer &&
      literalUnderFloor(node.initializer)
    ) {
      hits.push({ line: at(node), text: node.getText(sf) });
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return hits;
}

/** Reports every Tailwind `uppercase` utility in a string literal. */
function findUppercase(fileName: string, source: string): Hit[] {
  if (fileName.endsWith(".css")) return [];
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const hits: Hit[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      // A class token: whole word, optionally behind variants (`sm:uppercase`).
      if (/(?:^|\s)(?:[\w-]+:)*uppercase(?=\s|$)/.test(node.text)) {
        const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf));
        hits.push({ line: line + 1, text: node.text });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return hits;
}

function scan(find: (file: string, source: string) => Hit[]): Map<string, Hit[]> {
  const out = new Map<string, Hit[]>();
  for (const file of sourceFiles(SRC_ROOT)) {
    const rel = path.relative(FRONTEND_ROOT, file).split(path.sep).join("/");
    const hits = find(rel, fs.readFileSync(file, "utf8"));
    if (hits.length) out.set(rel, hits);
  }
  return out;
}

/** Files whose count differs from the allowed count, as readable lines. */
function overBudget(found: Map<string, Hit[]>, allowed: Record<string, number>): string[] {
  const problems: string[] = [];
  for (const [file, hits] of found) {
    if (hits.length !== (allowed[file] ?? 0)) {
      for (const h of hits) problems.push(`${file}:${h.line} ${h.text}`);
    }
  }
  for (const [file, count] of Object.entries(allowed)) {
    if (!found.has(file)) problems.push(`${file}: allowed ${count}, found 0 (lower the entry)`);
  }
  return problems;
}

describe("type floor", () => {
  it("renders no text below 12 px in src/", () => {
    expect(
      overBudget(scan(findSmallSizes), SIZE_EXCEPTIONS),
      "Text below 12 px is under Primer's floor. Use text-xs (12 px); in a " +
        "badge keep the height by trimming vertical padding instead.",
    ).toEqual([]);
  });

  it("uses no uppercase labels in src/", () => {
    expect(
      overBudget(scan(findUppercase), UPPERCASE_BASELINE),
      "Labels and headers are sentence case: text-xs font-semibold " +
        "text-muted-foreground. Korean has no case, so uppercase gives the two " +
        "languages different hierarchies. Data that is caps (CVE, SPDX ids) " +
        "is already caps and needs no class.",
    ).toEqual([]);
  });

  describe("size detector", () => {
    const count = (body: string) => findSmallSizes("x.tsx", body).length;

    it("flags arbitrary sizes under 12 px", () => {
      expect(count('const a = "px-1 text-[9px] font-medium";')).toBe(1);
      expect(count('const a = "text-[10px]";')).toBe(1);
      expect(count('const a = cn("x", ok && "text-[11px]");')).toBe(1);
      expect(count('const a = "text-[11.5px]";')).toBe(1);
      expect(count('const a = "text-[0.625rem]";')).toBe(1);
      expect(count("const a = `p-1 ${b} text-[10px]`;")).toBe(1);
      expect(count('const a = "md:text-[10px]";')).toBe(1);
    });

    it("flags inline font sizes under 12 px", () => {
      expect(count("const s = { fontSize: 10 };")).toBe(1);
      expect(count('const s = { fontSize: "11px" };')).toBe(1);
      expect(count('const s = { "font-size": "10px" };')).toBe(1);
      expect(count("const X = () => <text fontSize={9} />;")).toBe(1);
      expect(count('const X = () => <text fontSize="10" />;')).toBe(1);
    });

    it("flags CSS declarations under 12 px", () => {
      expect(findSmallSizes("x.css", ".a { font-size: 11px; }").length).toBe(1);
      expect(findSmallSizes("x.css", ".a { font-size: 0.75rem; }").length).toBe(0);
    });

    it("accepts 12 px and up, and leaves comments and copy alone", () => {
      expect(count('const a = "text-xs text-[12px] text-[13px] text-[0.75rem]";')).toBe(0);
      expect(count('const s = { fontSize: 12, "font-size": "14px" };')).toBe(0);
      expect(count('const s = { "min-zoomed-font-size": 7 };')).toBe(0);
      expect(count("// text-[10px] used to live here\nconst a = 1;")).toBe(0);
      expect(count("const X = () => <p>text-[10px]</p>;")).toBe(0);
    });
  });

  describe("uppercase detector", () => {
    const count = (body: string) => findUppercase("x.tsx", body).length;

    it("flags the uppercase utility in class strings", () => {
      expect(count('const a = "text-xs uppercase tracking-wide";')).toBe(1);
      expect(count('const a = "uppercase";')).toBe(1);
      expect(count('const a = cn("x", "sm:uppercase");')).toBe(1);
      expect(count("const a = `px-1 uppercase ${b}`;")).toBe(1);
    });

    it("leaves comments, copy and other words alone", () => {
      expect(count("// uppercase + tracking-wide used to be here\nconst a = 1;")).toBe(0);
      expect(count('const a = "normal-case";')).toBe(0);
      expect(count('const a = "id must be all-uppercase";')).toBe(0);
      expect(count("const X = () => <p>uppercase</p>;")).toBe(0);
    });
  });
});
