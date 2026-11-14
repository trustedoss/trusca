/**
 * The Primer overrides file (src/primer.css).
 *
 * Primer's theme CSS sets its colours as custom properties on
 * `[data-color-mode][data-light-theme]`. Our overrides only work if they (a)
 * out-rank that selector, (b) name variables Primer really defines, and (c) take
 * their colours from index.css, so the navy and the fonts exist once.
 *
 * (a) is the failure that bit another project: with two attributes in the
 * selector the primary button stayed green and nothing reported it.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  cmp,
  parseDeclarations,
  parseRules,
  readCssTree,
  specificity,
  splitSelectors,
} from "./helpers/cssRules";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = path.join(__dirname, "..", "..", "..");
const read = (...p: string[]) => fs.readFileSync(path.join(FRONTEND_ROOT, ...p), "utf8");

const primerCss = read("src", "primer.css");
const indexCss = read("src", "index.css");
const THEMES = path.join("node_modules", "@primer", "primitives", "dist", "css", "functional", "themes");
const PRIMITIVES = path.join("node_modules", "@primer", "primitives", "dist", "css");

const THREE = "[data-color-mode][data-light-theme][data-dark-theme]";

/** Variable rules only: the left-bar rules in the same file declare `display`. */
const variableRules = parseRules(primerCss).filter((r) =>
  parseDeclarations(r.body).some(([n]) => n.startsWith("--")),
);
/**
 * Rules that set a variable on <html>, next to Primer's theme blocks. Only
 * these compete with the theme selectors, so only these need the three
 * attributes and the specificity.
 */
const overrideRules = variableRules.filter((r) => r.selector.includes("[data-color-mode"));
/**
 * Rules that set a variable on a descendant (the ink-coloured global bar). A
 * declaration on the element itself beats the value it would inherit from
 * <html>, whatever the selector weights, so no specificity is needed. They
 * still must name real Primer variables and take their colours from tokens.
 */
const scopedRules = variableRules.filter((r) => !overrideRules.includes(r));
const overrides = variableRules.flatMap((r) => parseDeclarations(r.body));

/** Token name -> how its value is spelled (HSL channels or a complete colour). */
function indexTokens(): Map<string, "channels" | "colour"> {
  const out = new Map<string, "channels" | "colour">();
  for (const m of indexCss.matchAll(/^\s*--([a-z0-9-]+):\s*([^;]+);/gm)) {
    const value = m[2].replace(/\/\*.*?\*\//g, "").trim();
    out.set(m[1], /^-?[\d.]+\s+[\d.]+%\s+[\d.]+%$/.test(value) ? "channels" : "colour");
  }
  return out;
}

/** Every custom property the primitives files we import define (themes and the rest). */
function primerVariables(): Set<string> {
  const names = new Set<string>();
  for (const { css } of readCssTree(path.join(FRONTEND_ROOT, PRIMITIVES))) {
    for (const m of css.matchAll(/^\s*(--[A-Za-z0-9-]+):/gm)) names.add(m[1]);
  }
  return names;
}

describe("primer.css selector", () => {
  it("finds override rules to police", () => {
    expect(overrideRules.length).toBeGreaterThan(0);
    expect(scopedRules.length).toBeGreaterThan(0);
    expect(overrides.length).toBeGreaterThan(20);
  });

  it("carries all three colour-mode attributes on every rule that overrides a variable", () => {
    for (const r of overrideRules) {
      for (const sel of splitSelectors(r.selector)) {
        expect(sel, `selector "${sel}"`).toContain("[data-color-mode");
        expect(sel, `selector "${sel}"`).toContain("[data-light-theme");
        expect(sel, `selector "${sel}"`).toContain("[data-dark-theme");
      }
    }
    expect(primerCss).toContain(THREE);
  });

  it("out-ranks every selector of the primitives light and dark themes", () => {
    const ours = overrideRules.flatMap((r) => splitSelectors(r.selector).map(specificity));
    const weakest = ours.reduce((a, b) => (cmp(b, a) < 0 ? b : a));
    for (const file of ["light.css", "dark.css"]) {
      // The `auto` selectors serve colour mode "auto", which nothing writes
      // (themeGuard.test.ts pins the attribute to light or dark).
      const theirs = parseRules(read(THEMES, file))
        .flatMap((r) => splitSelectors(r.selector))
        .filter((s) => !s.includes('="auto"'))
        .map((s) => ({ s, spec: specificity(s) }));
      expect(theirs.length).toBeGreaterThan(0);
      for (const t of theirs) {
        expect(cmp(weakest, t.spec), `${file}: ${t.s}`).toBeGreaterThan(0);
      }
    }
  });
});

describe("primer.css values", () => {
  const tokens = indexTokens();
  const primerVars = primerVariables();

  it("reads index.css tokens in both spellings", () => {
    // Guards the classifier below: with one spelling missing, the format check
    // would pass for the wrong reason.
    expect(tokens.get("primary")).toBe("channels");
    expect(tokens.get("brand")).toBe("colour");
  });

  it("only overrides variables Primer defines", () => {
    for (const [name] of overrides) {
      expect(primerVars.has(name), `${name} is not a Primer variable, so it would do nothing`).toBe(true);
    }
  });

  const colourOverrides = overrides.filter(([n]) => !n.startsWith("--fontStack-"));

  it.each(colourOverrides)("%s points at an index.css token", (name, value) => {
    const bare = value.trim();
    if (bare === "transparent" || bare === "none") return;

    expect(value, `${name} holds a literal colour`).not.toMatch(
      /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(\s*[\d.]|\boklch\(|\blab\(/,
    );

    const refs = [...value.matchAll(/(hsl\(\s*)?var\(--([A-Za-z0-9-]+)/g)];
    const ours = refs.filter((m) => tokens.has(m[2]));
    expect(ours.length, `${name}: ${value} uses no index.css token`).toBeGreaterThan(0);

    for (const m of refs) {
      if (!tokens.has(m[2])) {
        expect(primerVars.has(`--${m[2]}`), `${name} reads --${m[2]}, defined nowhere`).toBe(true);
        continue;
      }
      const wrapped = Boolean(m[1]);
      expect(
        wrapped,
        `${name}: --${m[2]} is ${tokens.get(m[2])}, so it ${wrapped ? "must not" : "must"} sit inside hsl()`,
      ).toBe(tokens.get(m[2]) === "channels");
    }
  });

  it("names the app's two typefaces", () => {
    const get = (n: string) => overrides.find(([k]) => k === n)?.[1] ?? "";
    expect(get("--fontStack-sansSerif")).toMatch(/^"Inter"/);
    expect(get("--fontStack-monospace")).toMatch(/^"JetBrains Mono"/);
    expect(get("--fontStack-system")).toContain("var(--fontStack-sansSerif)");
  });
});
