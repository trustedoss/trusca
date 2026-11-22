/**
 * Octicons are drawn for 12, 16 and 24 px only.
 *
 * The icon set ships hand-tuned paths for those three sizes; any other number
 * scales the nearest one and the strokes land between pixels, so a 14 px
 * chevron reads soft next to a crisp 16 px one in the same row. After the move
 * to Primer about fifty call sites still carried the old `size={14}` and two
 * set the size through `h-3.5 w-3.5`. Nothing else fails on either, because
 * the component accepts any number and the class is valid Tailwind.
 *
 * The guard parses every source file and looks at JSX elements that render an
 * octicon: a name imported from `@primer/octicons-react`, or a local such as
 * `Icon` or `StatusIcon` that holds one (an icon picked from a lookup table).
 * Components declared in the same file or imported from elsewhere are left
 * alone even when their name ends in `Icon`.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";
import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");
const SRC_ROOT = path.join(FRONTEND_ROOT, "src");

const OCTICONS_MODULE = "@primer/octicons-react";
const ALLOWED_PX = new Set([12, 16, 24]);
/** Primer's named sizes map onto 16, 32 and 64; only "small" is a drawn size. */
const ALLOWED_NAMED = new Set(["small"]);
/** Tailwind spacing steps that equal an allowed pixel size (3 = 12, 4 = 16, 6 = 24). */
const ALLOWED_STEPS = new Set(["3", "4", "6"]);

interface Violation {
  file: string;
  line: number;
  text: string;
}

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) sourceFiles(full, out);
    else if (/\.tsx$/.test(entry.name)) out.push(full);
  }
  return out;
}

function collectStrings(node: ts.Node, out: string[]): void {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    out.push(node.text);
  }
  ts.forEachChild(node, (child) => collectStrings(child, out));
}

function collectNumbers(node: ts.Node, out: number[]): void {
  if (ts.isNumericLiteral(node)) out.push(Number(node.text));
  ts.forEachChild(node, (child) => collectNumbers(child, out));
}

/** Reports every octicon element in `source` whose size is not 12, 16 or 24. */
function findOffSizeOcticons(fileName: string, source: string): Violation[] {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const octicons = new Set<string>();
  const otherComponents = new Set<string>();

  for (const stmt of sf.statements) {
    if (ts.isImportDeclaration(stmt) && ts.isStringLiteral(stmt.moduleSpecifier)) {
      const fromOcticons = stmt.moduleSpecifier.text === OCTICONS_MODULE;
      const clause = stmt.importClause;
      if (clause?.name) (fromOcticons ? octicons : otherComponents).add(clause.name.text);
      const bindings = clause?.namedBindings;
      if (bindings && ts.isNamedImports(bindings)) {
        for (const el of bindings.elements) {
          (fromOcticons ? octicons : otherComponents).add(el.name.text);
        }
      }
    }
  }
  const visitDecl = (node: ts.Node): void => {
    if ((ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) && node.name) {
      otherComponents.add(node.name.text);
    }
    ts.forEachChild(node, visitDecl);
  };
  visitDecl(sf);

  const isOcticon = (tag: string): boolean =>
    octicons.has(tag) || (/(^|[a-z])Icon$/.test(tag) && !otherComponents.has(tag));

  const violations: Violation[] = [];
  const report = (node: ts.Node): void => {
    const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf));
    violations.push({ file: fileName, line: line + 1, text: node.getText(sf) });
  };

  const visit = (node: ts.Node): void => {
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      const tag = node.tagName.getText(sf);
      if (isOcticon(tag)) {
        for (const attr of node.attributes.properties) {
          if (!ts.isJsxAttribute(attr) || !attr.initializer) continue;
          const name = attr.name.getText(sf);
          if (name === "size") {
            const init = attr.initializer;
            if (ts.isStringLiteral(init)) {
              if (!ALLOWED_NAMED.has(init.text)) report(attr);
              continue;
            }
            const numbers: number[] = [];
            const strings: string[] = [];
            collectNumbers(init, numbers);
            collectStrings(init, strings);
            if (
              numbers.some((n) => !ALLOWED_PX.has(n)) ||
              strings.some((s) => !ALLOWED_NAMED.has(s))
            ) {
              report(attr);
            }
          } else if (name === "className") {
            const strings: string[] = [];
            collectStrings(attr.initializer, strings);
            const tokens = strings.join(" ").split(/\s+/);
            const offSize = tokens.some((token) => {
              const m = /^(?:h|w|size)-(\d+(?:\.\d+)?|\[[^\]]+\])$/.exec(token);
              return m !== null && !ALLOWED_STEPS.has(m[1]);
            });
            if (offSize) report(attr);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return violations;
}

describe("octicon sizes", () => {
  it("finds octicon imports to check", () => {
    // Without this the suite would pass vacuously if the import detection
    // stopped matching.
    const withImports = sourceFiles(SRC_ROOT).filter((f) =>
      fs.readFileSync(f, "utf8").includes(OCTICONS_MODULE),
    );
    expect(withImports.length).toBeGreaterThan(50);
  });

  it("uses only 12, 16 or 24 px in src/", () => {
    const violations = sourceFiles(SRC_ROOT).flatMap((file) =>
      findOffSizeOcticons(
        path.relative(FRONTEND_ROOT, file).split(path.sep).join("/"),
        fs.readFileSync(file, "utf8"),
      ),
    );
    expect(
      violations.map((v) => `${v.file}:${v.line} ${v.text}`),
      "Octicons are drawn for 12, 16 and 24 px. Use 16 beside 14 px text and " +
        "in default buttons, 12 in small buttons, badges and 12 px text.",
    ).toEqual([]);
  });

  describe("detector", () => {
    const check = (body: string, imports = `import { ChevronDownIcon } from "${OCTICONS_MODULE}";`) =>
      findOffSizeOcticons("x.tsx", `${imports}\nexport const X = () => (${body});`).length;

    it("flags a 14 on an imported octicon", () => {
      expect(check("<ChevronDownIcon size={14} />")).toBe(1);
    });

    it("flags an aliased import", () => {
      expect(
        check("<Chevron size={20} />", `import { ChevronDownIcon as Chevron } from "${OCTICONS_MODULE}";`),
      ).toBe(1);
    });

    it("flags a size set through Tailwind classes", () => {
      expect(check('<ChevronDownIcon className="h-3.5 w-3.5 shrink-0" />')).toBe(1);
      expect(check('<ChevronDownIcon className={cn("h-5 w-5", a && "x")} />')).toBe(1);
    });

    it("flags a local that holds an octicon", () => {
      expect(check("<Icon size={14} />", "")).toBe(1);
      expect(check("<StatusIcon size={32} />", "")).toBe(1);
    });

    it("flags an off size inside a conditional", () => {
      expect(check("<ChevronDownIcon size={compact ? 12 : 14} />")).toBe(1);
    });

    it("accepts the drawn sizes and no size at all", () => {
      expect(check("<ChevronDownIcon size={12} />")).toBe(0);
      expect(check("<ChevronDownIcon size={16} />")).toBe(0);
      expect(check("<ChevronDownIcon size={24} />")).toBe(0);
      expect(check('<ChevronDownIcon size="small" />')).toBe(0);
      expect(check('<ChevronDownIcon className="h-4 w-4 shrink-0" />')).toBe(0);
      expect(check("<ChevronDownIcon />")).toBe(0);
    });

    it("leaves components that are not octicons alone", () => {
      expect(check("<BrandMark size={22} />", "")).toBe(0);
      expect(
        check("<ProviderIcon size={20} />", 'import { ProviderIcon } from "./provider-icon";'),
      ).toBe(0);
      expect(check("<GitHubIcon size={20} />", "function GitHubIcon() { return null; }")).toBe(0);
    });
  });
});
