/**
 * Router ↔ document-title contract.
 *
 * `index.html` named the tab "TRUSCA" and nothing changed it afterwards, so
 * every tab, history entry and bookmark carried the same four letters. Two
 * projects open side by side were indistinguishable.
 *
 * Most screens now get a title from `PageHeader`, which already knows the page
 * name; the rest call `useDocumentTitle` directly. Either is fine — what this
 * guards is that a screen added later cannot quietly have neither. Same shape
 * as `visualCoverage.test.ts`, and the same hardening rule behind it: the
 * screen vocabulary lives in `router.tsx` and the title mechanism lives in the
 * screens, so something has to hold the two together.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");
const SRC_ROOT = path.join(FRONTEND_ROOT, "src");
const ROUTER_PATH = path.join(SRC_ROOT, "router.tsx");

/**
 * Components that mount routes rather than being one: layout wrappers, the
 * auth guard, and the redirect element. They have no name of their own to put
 * in the tab.
 */
const NOT_A_SCREEN = new Set([
  "AppShell",
  "RequireAuth",
  "AdminLayout",
  "Navigate",
]);

/**
 * Screens that deliberately leave the tab title to something else, with the
 * reason. Each is asserted to still be routed, so an entry cannot rot into a
 * silent exemption for a screen that no longer exists.
 */
const EXEMPT: Record<string, string> = {
  DesignSystemPreview:
    "Dev-only route, not part of the product surface and not built in prod.",
};

/** `element={<Xxx ... />}` in the route table. */
function routedComponents(): string[] {
  const source = fs.readFileSync(ROUTER_PATH, "utf8");
  const names = new Set<string>();
  for (const match of source.matchAll(/element=\{<([A-Z][A-Za-z0-9]*)/g)) {
    if (!NOT_A_SCREEN.has(match[1])) names.add(match[1]);
  }
  return [...names].sort();
}

/** Map a routed component name to the file the router imports it from. */
function importedPaths(): Map<string, string> {
  const source = fs.readFileSync(ROUTER_PATH, "utf8");
  const paths = new Map<string, string>();
  const pattern =
    /import\s+(?:\{\s*([A-Za-z0-9_,\s]+?)\s*\}|([A-Za-z0-9_]+))\s+from\s+"@\/([^"]+)"/g;
  for (const match of source.matchAll(pattern)) {
    const names = (match[1] ?? match[2]).split(",").map((n) => n.trim());
    for (const name of names) {
      if (name) paths.set(name, match[3]);
    }
  }
  return paths;
}

function readScreen(relative: string): string {
  for (const ext of [".tsx", ".ts"]) {
    const full = path.join(SRC_ROOT, `${relative}${ext}`);
    if (fs.existsSync(full)) return fs.readFileSync(full, "utf8");
  }
  throw new Error(`could not find a source file for @/${relative}`);
}

/** Blank comments in place so a mention in prose does not read as usage. */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/[^\n]*/g, (match, lead: string) =>
      lead + " ".repeat(match.length - lead.length),
    );
}

/**
 * A screen names the tab if it renders one of the two layout components that
 * already receive a page name (`PageHeader` after auth, `AuthLayout` before
 * it), or if it calls the hook itself.
 *
 * Rendering is not enough on its own: three detail screens define a local
 * component *called* `PageHeader` at the bottom of their own file, and a
 * name match alone would accept one of those while it set no title. So the
 * shared component has to be imported as well as rendered. (Verified by
 * building exactly that shape and watching this test pass before the import
 * check was added.)
 */
function namesTheTab(source: string): boolean {
  const code = stripComments(source);
  const usesShared = (tag: string, module: string) =>
    new RegExp(`<${tag}\\b`).test(code) &&
    new RegExp(`from\\s+"@/${module}"`).test(code);

  return (
    usesShared("PageHeader", "components/PageHeader") ||
    usesShared("AuthLayout", "pages/auth/AuthLayout") ||
    /\buseDocumentTitle\s*\(/.test(code)
  );
}

describe("document-title coverage", () => {
  const routed = routedComponents();
  const paths = importedPaths();

  it("finds the routed screens (guards against an empty sweep)", () => {
    expect(routed.length).toBeGreaterThanOrEqual(20);
  });

  it("resolves every routed screen to a source file", () => {
    const unresolved = routed.filter((name) => !paths.has(name));
    expect(unresolved).toEqual([]);
  });

  it("gives every routed screen a tab name", () => {
    const missing = routed
      .filter((name) => !(name in EXEMPT))
      .filter((name) => !namesTheTab(readScreen(paths.get(name)!)))
      .sort();

    expect(
      missing,
      "these screens leave the tab titled TRUSCA. Render a PageHeader, or " +
        "call useDocumentTitle, or add an entry to EXEMPT with the reason.",
    ).toEqual([]);
  });

  it("still routes every exempted screen", () => {
    for (const name of Object.keys(EXEMPT)) {
      expect(routed, `EXEMPT names ${name}, which is no longer routed`).toContain(
        name,
      );
    }
  });
});
