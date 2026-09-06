#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Full-module mock ratchet (issue #390).
 *
 * `vi.mock("@/lib/api", () => ({ ... }))` replaces the entire module with the
 * object literal the factory returns. When the real module gains an export
 * (a new API call, a new constant), every test that already blanket-mocks it
 * keeps compiling, keeps rendering, and silently hands that export
 * `undefined` to whatever imports it. A type guard built on a discriminated
 * union (`postLogin`'s MFA-required branch) reads `undefined` as falsy and
 * takes the wrong branch, and the test that broke is rarely the one whose PR
 * added the export; it is whichever unrelated test happens to render a
 * component that imports the same module.
 *
 * The sanctioned fix already exists and is well used (63 call sites at the
 * time this gate was written): `importOriginal` / `importActual`, which
 * spreads the real module and overrides only the exports the test cares
 * about. A mock built that way tracks the source automatically. A mock that
 * hand-writes every key does not, and cannot, since the whole point of
 * writing it by hand was to avoid touching the real module.
 *
 * This does not migrate the 51 existing full mocks (`@/lib/api` x 30,
 * `@/lib/projectsApi` x 21); that is a larger, separate change and each one
 * has to be checked against what the component under test actually renders.
 * What this adds is the same ratchet `token-lint.mjs` and
 * `problem-detail-lint.mjs` use: freeze the current count per file, let it
 * only go down, and fail on anything new.
 *
 * A full mock whose factory omits an export the real module currently
 * declares is flagged as a gap in the report (the failure mode this issue
 * describes, happening today), but a gap in a file the baseline already
 * covers does not fail the gate by itself; only a rise in that file's
 * full-mock count does. Nearly every one of the 51 existing mocks already
 * has a gap of this kind (see `mock-audit-baseline.json`), and this gate
 * exists to stop that number from growing, not to demand the whole tree fix
 * itself in one PR. The gap is still surfaced, prominently, because it is
 * the actual bug.
 *
 * Usage
 *   node scripts/mock-audit.mjs             # check (CI)
 *   node scripts/mock-audit.mjs --update    # re-record the baseline
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import ts from "typescript";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.resolve(__dirname, "..");
const TESTS_ROOT = path.join(FRONTEND_ROOT, "tests");
const SRC_ROOT = path.join(FRONTEND_ROOT, "src");
const BASELINE_PATH = path.join(__dirname, "mock-audit-baseline.json");

const SOURCE_EXTENSIONS = [".ts", ".tsx"];

function* walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      yield* walk(full);
    } else if (/\.tsx?$/.test(entry.name)) {
      yield full;
    }
  }
}

function parseFile(filePath) {
  const text = fs.readFileSync(filePath, "utf8");
  const kind = filePath.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(filePath, text, ts.ScriptTarget.Latest, true, kind);
}

/**
 * A factory only ever takes a parameter to receive vitest's `importOriginal`
 * callback (`vi.mock("x", async (importOriginal) => {...})`), so any factory
 * that declares one is a partial mock by construction. The other idiom in
 * this codebase calls `vi.importActual(...)` directly inside a zero-arg
 * factory, so the body is checked too, matching either identifier and not
 * just the literal spread-of-`actual` shape, so a partial mock that only
 * *overrides* fields (no spread at all) still counts as partial.
 */
function isPartialMockFactory(factory) {
  if (factory.parameters.length > 0) return true;
  return /\bimportOriginal\b|\bimportActual\b/.test(factory.getText());
}

function propertyName(name) {
  if (ts.isIdentifier(name) || ts.isPrivateIdentifier(name)) return name.text;
  if (ts.isStringLiteralLike(name)) return name.text;
  if (ts.isNumericLiteral(name)) return name.text;
  return null; // computed key, cannot be determined statically
}

/** Top-level `return <object literal>` statements directly inside `block`,
 * not reached through a nested function. Real factories in this codebase are
 * a single `return {...}`; branching returns are unioned defensively. */
function collectReturnedObjects(block, out) {
  const visit = (node) => {
    if (ts.isFunctionLike(node)) return; // don't cross into nested closures
    if (ts.isReturnStatement(node) && node.expression && ts.isObjectLiteralExpression(node.expression)) {
      out.push(node.expression);
    }
    ts.forEachChild(node, visit);
  };
  for (const stmt of block.statements) visit(stmt);
}

/**
 * The set of top-level keys a full-mock factory declares, or `null` if the
 * shape can't be read statically (a computed key, or a spread of something
 * other than the real module, which `isPartialMockFactory` already routes
 * around, but a spread of an unrelated local object would land here).
 * `null` means "don't claim to know this mock's shape", not "empty".
 */
function factoryDeclaredKeys(factory) {
  const objects = [];
  let body = factory.body;
  if (!ts.isBlock(body)) {
    if (ts.isParenthesizedExpression(body)) body = body.expression;
    if (!ts.isObjectLiteralExpression(body)) return null;
    objects.push(body);
  } else {
    collectReturnedObjects(body, objects);
    if (objects.length === 0) return null;
  }

  const keys = new Set();
  for (const obj of objects) {
    for (const prop of obj.properties) {
      if (ts.isSpreadAssignment(prop)) return null;
      if (
        ts.isPropertyAssignment(prop) ||
        ts.isShorthandPropertyAssignment(prop) ||
        ts.isMethodDeclaration(prop) ||
        ts.isGetAccessor(prop) ||
        ts.isSetAccessor(prop)
      ) {
        const name = propertyName(prop.name);
        if (name === null) return null;
        keys.add(name);
      }
    }
  }
  return keys;
}

/** Resolve a `vi.mock` specifier to a source file. Bare specifiers
 * (npm packages) return `null`; this gate only diffs first-party modules,
 * where the export set is ours to keep in sync. */
function resolveSpecifier(specifier, fromFile) {
  let base;
  if (specifier.startsWith("@/")) {
    base = path.join(SRC_ROOT, specifier.slice(2));
  } else if (specifier.startsWith(".")) {
    base = path.resolve(path.dirname(fromFile), specifier);
  } else {
    return null;
  }

  const candidates = [
    base,
    ...SOURCE_EXTENSIONS.map((ext) => base + ext),
    ...SOURCE_EXTENSIONS.map((ext) => path.join(base, "index" + ext)),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return null;
}

/**
 * The module's value-level export names: the ones that exist at runtime and
 * so are the ones a hand-written mock has to cover. Type aliases and
 * interfaces are erased by the compiler and are never in scope; a mock that
 * omits them is not a gap.
 */
function realValueExports(filePath, visited = new Set()) {
  const resolved = path.resolve(filePath);
  if (visited.has(resolved)) return new Set();
  visited.add(resolved);

  const source = parseFile(resolved);
  const names = new Set();

  for (const stmt of source.statements) {
    const modifiers = ts.canHaveModifiers(stmt) ? ts.getModifiers(stmt) ?? [] : [];
    const hasExport = modifiers.some((m) => m.kind === ts.SyntaxKind.ExportKeyword);
    const hasDefault = modifiers.some((m) => m.kind === ts.SyntaxKind.DefaultKeyword);

    if (ts.isVariableStatement(stmt) && hasExport) {
      for (const decl of stmt.declarationList.declarations) {
        if (ts.isIdentifier(decl.name)) names.add(decl.name.text);
      }
    } else if (
      (ts.isFunctionDeclaration(stmt) || ts.isClassDeclaration(stmt)) &&
      hasExport
    ) {
      if (hasDefault) names.add("default");
      else if (stmt.name) names.add(stmt.name.text);
    } else if (ts.isEnumDeclaration(stmt) && hasExport) {
      names.add(stmt.name.text);
    } else if (ts.isExportAssignment(stmt) && !stmt.isExportEquals) {
      names.add("default");
    } else if (ts.isExportDeclaration(stmt)) {
      if (stmt.isTypeOnly) continue;
      if (stmt.moduleSpecifier && ts.isStringLiteralLike(stmt.moduleSpecifier)) {
        const target = resolveSpecifier(stmt.moduleSpecifier.text, resolved);
        if (stmt.exportClause && ts.isNamespaceExport(stmt.exportClause)) {
          // `export * as ns from "./x"`: a single namespace-object export.
          names.add(stmt.exportClause.name.text);
          continue;
        }
        if (!stmt.exportClause) {
          // `export * from "./x"`: merge the target's own value exports.
          if (target) for (const n of realValueExports(target, visited)) names.add(n);
          continue;
        }
      }
      if (stmt.exportClause && ts.isNamedExports(stmt.exportClause)) {
        for (const spec of stmt.exportClause.elements) {
          if (spec.isTypeOnly) continue;
          names.add(spec.name.text);
        }
      }
    }
  }

  return names;
}

/**
 * Scan `tests/**` for full-module `vi.mock` calls.
 *
 * @returns {{
 *   counts: Record<string, number>,
 *   findings: Array<{file: string, line: number, module: string, resolved: boolean, missing: string[] | null}>
 * }}
 */
export function scan(testsRoot = TESTS_ROOT, frontendRoot = FRONTEND_ROOT) {
  const counts = {};
  const findings = [];

  for (const absolute of walk(testsRoot)) {
    const rel = path.relative(frontendRoot, absolute).split(path.sep).join("/");
    const source = parseFile(absolute);

    const visit = (node) => {
      if (
        ts.isCallExpression(node) &&
        ts.isPropertyAccessExpression(node.expression) &&
        ts.isIdentifier(node.expression.expression) &&
        node.expression.expression.text === "vi" &&
        node.expression.name.text === "mock" &&
        node.arguments.length >= 2 &&
        ts.isStringLiteralLike(node.arguments[0])
      ) {
        const specifierNode = node.arguments[0];
        const factory = node.arguments[1];
        const isFactory =
          ts.isArrowFunction(factory) || ts.isFunctionExpression(factory);

        if (isFactory && !isPartialMockFactory(factory)) {
          const specifier = specifierNode.text;
          const { line } = source.getLineAndCharacterOfPosition(node.getStart());
          const targetFile = resolveSpecifier(specifier, absolute);
          const declaredKeys = factoryDeclaredKeys(factory);

          let missing = null;
          if (targetFile && declaredKeys) {
            const real = realValueExports(targetFile);
            missing = [...real].filter((name) => !declaredKeys.has(name)).sort();
          }

          counts[rel] = (counts[rel] ?? 0) + 1;
          findings.push({
            file: rel,
            line: line + 1,
            module: specifier,
            resolved: targetFile !== null,
            missing,
          });
        }
      }
      ts.forEachChild(node, visit);
    };
    ts.forEachChild(source, visit);
  }

  // Gaps first: that is the failure mode this gate exists to surface.
  findings.sort((a, b) => {
    const aGap = a.missing && a.missing.length > 0 ? 0 : 1;
    const bGap = b.missing && b.missing.length > 0 ? 0 : 1;
    if (aGap !== bGap) return aGap - bGap;
    if (a.file !== b.file) return a.file.localeCompare(b.file);
    return a.line - b.line;
  });

  return { counts, findings };
}

/**
 * Compare a fresh scan against the recorded baseline. Same shape as
 * `token-lint.mjs`'s ratchet: a file with no budget is new debt, a file over
 * budget grew, and a file under budget must have its lowered count committed
 * so the gain cannot be quietly re-spent.
 *
 * @returns {{ok: boolean, added: Array, grew: Array, shrank: Array, total: number, baselineTotal: number}}
 */
export function diff(counts, baseline) {
  const added = [];
  const grew = [];
  const shrank = [];

  for (const [file, count] of Object.entries(counts)) {
    const budget = baseline[file] ?? 0;
    if (budget === 0) added.push({ file, count });
    else if (count > budget) grew.push({ file, count, budget });
    else if (count < budget) shrank.push({ file, count, budget });
  }
  for (const [file, budget] of Object.entries(baseline)) {
    if (!(file in counts)) shrank.push({ file, count: 0, budget });
  }

  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const baselineTotal = Object.values(baseline).reduce((a, b) => a + b, 0);

  return {
    ok: added.length === 0 && grew.length === 0 && shrank.length === 0,
    added,
    grew,
    shrank,
    total,
    baselineTotal,
  };
}

function main() {
  const update = process.argv.includes("--update");
  const { counts, findings } = scan();

  if (update) {
    const sorted = Object.fromEntries(
      Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)),
    );
    fs.writeFileSync(BASELINE_PATH, `${JSON.stringify(sorted, null, 2)}\n`);
    const total = Object.values(sorted).reduce((a, b) => a + b, 0);
    const gaps = findings.filter((f) => f.missing && f.missing.length > 0).length;
    console.log(
      `mock-audit: baseline updated, ${total} full-module mocks across ` +
        `${Object.keys(sorted).length} file(s), ${gaps} with an undeclared-export gap.`,
    );
    return 0;
  }

  if (!fs.existsSync(BASELINE_PATH)) {
    console.error(
      "mock-audit: no baseline found. Run `node scripts/mock-audit.mjs --update` once and commit it.",
    );
    return 1;
  }

  const baseline = JSON.parse(fs.readFileSync(BASELINE_PATH, "utf8"));
  const result = diff(counts, baseline);

  const byFile = (file) => findings.filter((f) => f.file === file);
  const describe = (f) => {
    const tag = f.missing && f.missing.length > 0 ? "GAP" : f.resolved ? "ok " : "ext";
    const detail =
      f.missing && f.missing.length > 0
        ? ` (missing ${f.missing.join(", ")})`
        : "";
    return `${f.file}:${f.line}  [${tag}] ${f.module}${detail}`;
  };

  if (result.ok) {
    const gaps = findings.filter((f) => f.missing && f.missing.length > 0).length;
    console.log(
      `mock-audit: OK, ${result.total} known full-module mock(s), none added ` +
        `(budget ${result.baselineTotal}). ${gaps} pre-existing gap(s); see ` +
        "the findings above if this baseline was just updated.",
    );
    return 0;
  }

  if (result.added.length > 0) {
    console.error(
      "\nmock-audit: NEW full-module mock(s). Prefer `importOriginal` /\n" +
        "`vi.importActual` so the mock tracks the real module's exports;\n" +
        "see tests/unit/features/exportClients.test.ts:40 for the pattern.\n",
    );
    for (const { file } of result.added) {
      for (const f of byFile(file)) console.error(`  ${describe(f)}`);
    }
  }

  if (result.grew.length > 0) {
    console.error("\nmock-audit: full-module mock count INCREASED in these files.\n");
    for (const { file, count, budget } of result.grew) {
      console.error(`  ${file}  ${budget} → ${count}`);
      for (const f of byFile(file)) console.error(`      ${describe(f)}`);
    }
  }

  if (result.shrank.length > 0) {
    console.error(
      "\nmock-audit: full-module mock count DECREASED, commit the lowered",
    );
    console.error("            baseline so the gain is locked in:\n");
    console.error("              node scripts/mock-audit.mjs --update\n");
    for (const { file, count, budget } of result.shrank) {
      console.error(`  ${file}  ${budget} → ${count}`);
    }
  }

  console.error(
    `\ntotal ${result.total}, baseline ${result.baselineTotal}.`,
  );
  return 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  process.exit(main());
}
