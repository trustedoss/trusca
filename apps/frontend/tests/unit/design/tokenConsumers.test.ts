/**
 * G0-6 — every colour token declared must be used.
 *
 * W14 shipped `--brand-strong` and `--brand-foreground` with no consumer
 * anywhere in `src/`, and one of them carried a contrast assertion, so the
 * suite was defending a colour the product never paints. The same wave
 * documented an accent "whitelist" naming two surfaces that had no brand
 * token at all. Both are the same failure: a declaration that describes an
 * intention rather than the code.
 *
 * Nothing catches this otherwise. The token lint (`scripts/token-lint.mjs`)
 * runs the other direction — it stops call sites bypassing tokens, not
 * tokens nobody calls. Dead tokens are not merely tidiness: they read as
 * available vocabulary, so the next person uses one, discovers it was never
 * checked against a real surface, and the contrast guarantee turns out to
 * have been theoretical.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");
const SRC_ROOT = path.join(FRONTEND_ROOT, "src");
const CSS_PATH = path.join(SRC_ROOT, "index.css");
/**
 * The Primer overrides map Primer's variables onto our tokens, so they are a
 * consumer in their own right. When screens move to Primer components the
 * Tailwind utilities that used to consume `--brand` and friends disappear; the
 * tokens must stay "used" through this file or this guard would fail the
 * migration for removing code it should be happy to lose.
 */
const PRIMER_CSS_PATH = path.join(SRC_ROOT, "primer.css");

/**
 * Token families in scope: the colour vocabularies this project owns.
 *
 * The shadcn base set (`--background`, `--muted`, …) is excluded — it is
 * upstream's contract, and a temporarily unused one is not a defect on our
 * side. Layout and motion tokens are excluded because they are consumed
 * through Tailwind keys (`w-sidebar`, `duration-fast`) whose names do not
 * contain the token slug.
 */
const FAMILIES = [/^brand(-|$)/, /^status-/, /^topbar(-|$)/, /^risk-.*-foreground$/];

/** Tailwind utilities that can carry a colour token. */
const UTILITIES = [
  "bg",
  "text",
  "border",
  "ring",
  "ring-offset",
  "fill",
  "stroke",
  "from",
  "via",
  "to",
  "outline",
  "shadow",
  "divide",
  "placeholder",
  "accent",
  "caret",
  "decoration",
];

/** Files that declare tokens rather than consume them. */
const DECLARATION_FILES = new Set(["src/index.css", "tailwind.config.ts"]);

function declaredTokens(): string[] {
  const css = fs.readFileSync(CSS_PATH, "utf8");
  const rootStart = css.indexOf(":root {");
  const darkStart = css.indexOf(".dark {", rootStart);
  const rootBlock = css.slice(rootStart, darkStart);

  const names = new Set<string>();
  for (const match of rootBlock.matchAll(/--([a-z0-9-]+):/g)) {
    if (FAMILIES.some((rx) => rx.test(match[1]))) names.add(match[1]);
  }
  return [...names].sort();
}

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      sourceFiles(full, out);
    } else if (/\.(ts|tsx|css)$/.test(entry.name)) {
      const rel = path.relative(FRONTEND_ROOT, full).split(path.sep).join("/");
      if (!DECLARATION_FILES.has(rel)) out.push(full);
    }
  }
  return out;
}

/** Whether `text` consumes the token, as a variable or as a Tailwind utility. */
function consumes(text: string, name: string): boolean {
  // The negative lookahead matters: `bg-status-success` must not be counted as
  // a use of `--status-success` when the file really says
  // `bg-status-success-subtle`.
  const asVariable = new RegExp(`var\\(--${name}[),]`);
  const asUtility = new RegExp(`\\b(?:${UTILITIES.join("|")})-${name}(?![\\w-])`);
  return asVariable.test(text) || asUtility.test(text);
}

describe("colour token consumers", () => {
  const sources = sourceFiles(SRC_ROOT)
    .concat(path.join(FRONTEND_ROOT, "tailwind.config.ts"))
    .filter((f) => {
      const rel = path.relative(FRONTEND_ROOT, f).split(path.sep).join("/");
      return !DECLARATION_FILES.has(rel);
    })
    .map((f) => fs.readFileSync(f, "utf8"))
    .join("\n");

  it("finds the token families it is supposed to police", () => {
    // Without this the suite would pass vacuously if the extraction regex
    // or the :root/.dark anchors ever stopped matching.
    expect(declaredTokens().length).toBeGreaterThan(15);
  });

  it.each(declaredTokens())("--%s is used somewhere in src/", (name) => {
    // Two ways a token legitimately reaches a component: the CSS variable
    // directly, or the Tailwind utility the config derives from it.
    expect(
      consumes(sources, name),
      `--${name} is declared in index.css but nothing consumes it. ` +
        `Use it or remove it — a token nobody paints with is vocabulary ` +
        `that has never been checked against a real surface.`,
    ).toBe(true);
  });
});

describe("Primer overrides as a token consumer", () => {
  const primerCss = fs.readFileSync(PRIMER_CSS_PATH, "utf8");

  it("is part of the scanned sources", () => {
    expect(sourceFiles(SRC_ROOT)).toContain(PRIMER_CSS_PATH);
  });

  it("keeps the accent tokens used when no Tailwind utility is left", () => {
    // The state after the migration: only primer.css still names them.
    for (const name of ["brand", "brand-subtle"]) {
      expect(consumes(primerCss, name), `--${name}`).toBe(true);
    }
  });

  it("recognises both spellings of a reference", () => {
    expect(consumes("a { --x: var(--brand); }", "brand")).toBe(true);
    expect(consumes("a { --x: color-mix(in srgb, var(--brand) 40%, transparent); }", "brand")).toBe(true);
    expect(consumes("a { --x: var(--brand-subtle); }", "brand")).toBe(false);
    expect(consumes("bg-brand-subtle", "brand")).toBe(false);
  });
});

