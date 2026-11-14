/**
 * No thick left accent bars from Primer.
 *
 * NavList, ActionList, FilteredActionList, TreeView and the UnderlineNav
 * overflow menu draw a decorative bar down the left edge of the current item
 * with `::after`. This design has no such bars, and the selectors that switch
 * them on differ per component (data-active, data-is-active-descendant,
 * data-input-focused plus data-first-child, aria-current), so a hand-kept list
 * would rot with every Primer upgrade.
 *
 * This test reads the INSTALLED @primer/react CSS, finds every rule that draws
 * such a bar, and requires a hiding rule in src/primer.css for each one that
 * (a) names the same component classes and the same state attributes and (b)
 * out-ranks it. A Primer upgrade that adds a bar fails here rather than
 * shipping.
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
const PRIMER_DIST = path.join(FRONTEND_ROOT, "node_modules", "@primer", "react", "dist");
const primerCss = fs.readFileSync(path.join(FRONTEND_ROOT, "src", "primer.css"), "utf8");

interface Bar {
  selector: string;
  file: string;
}

/** Compounds of a selector (`a.b > c[d]` -> `a.b`, `c[d]`), splitting outside brackets and parentheses. */
function compounds(selector: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of selector) {
    if (ch === "(" || ch === "[") depth++;
    if (ch === ")" || ch === "]") depth--;
    if (depth === 0 && /[\s>+~]/.test(ch)) {
      if (cur) out.push(cur);
      cur = "";
    } else cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

/**
 * The component classes a hiding rule has to name: those of the compound that
 * owns the pseudo-element and of any compound that carries a state. Wrapper
 * classes further up (`TreeViewRootUlStyles`) only raise specificity.
 * `.prc-ActionList-ActionListItem-So4vC` -> `ActionList-ActionListItem`.
 */
function componentClasses(selector: string): string[] {
  const parts = compounds(selector);
  const classesOf = (c: string) =>
    [...c.matchAll(/\.prc-([A-Za-z]+-[A-Za-z0-9]+)-[A-Za-z0-9_-]{4,6}(?![A-Za-z0-9_-])/g)].map((m) => m[1]);
  const relevant = parts.filter((c, i) => i === parts.length - 1 || stateTokens(c).length > 0);
  const found = relevant.flatMap(classesOf);
  if (found.length > 0) return found;
  // The owner is `[aria-current]` or similar: the nearest compound with a class names the component.
  for (let i = parts.length - 1; i >= 0; i--) {
    const c = classesOf(parts[i]);
    if (c.length > 0) return c;
  }
  return [];
}

/** The state a selector depends on, minus the `:not(...)` exclusions. */
function stateTokens(selector: string): string[] {
  const noNot = removeFunctional(selector, "not");
  const attrs = [...noNot.matchAll(/\[(data-[a-z-]+|aria-[a-z]+)/g)].map((m) => m[1]);
  const pseudo = [...noNot.matchAll(/:(focus|hover)(?![\w-])/g)].map((m) => `:${m[1]}`);
  return [...attrs, ...pseudo];
}

function removeFunctional(selector: string, fn: string): string {
  let s = selector;
  for (;;) {
    const at = s.indexOf(`:${fn}(`);
    if (at < 0) return s;
    let depth = 0;
    let i = at + fn.length + 1;
    for (; i < s.length; i++) {
      if (s[i] === "(") depth++;
      if (s[i] === ")" && --depth === 0) break;
    }
    s = s.slice(0, at) + s.slice(i + 1);
  }
}

/**
 * A rule draws a left bar when it paints a generated, absolutely positioned,
 * narrow box (<= 4px) pinned to the left edge: `left` set, or `inset` with a 0
 * in the left slot. Underlines (`height: 2px`, pinned to the bottom) and the
 * forced-colours colour swaps have no such box and are not matched.
 */
function drawsLeftBar(selector: string, body: string): boolean {
  if (!/::?after\b/.test(selector)) return false;
  const d = new Map(parseDeclarations(body));
  if (d.get("content") !== '""') return false;
  if (d.get("position") !== "absolute") return false;
  const width = d.get("width") ?? "";
  const narrow = /^(var\(--base-size-(2|4)\b.*\)|\.125rem|\.25rem|[24]px)$/.test(width);
  if (!narrow) return false;
  const left = d.get("left");
  if (left !== undefined) return left === "0" || /\*-1\)$/.test(left);
  const inset = d.get("inset");
  if (inset) {
    const parts = inset.split(/\s+(?![^(]*\))/);
    return parts.length === 4 && parts[3] === "0";
  }
  return false;
}

function findBars(): Bar[] {
  const bars: Bar[] = [];
  for (const { file, css } of readCssTree(PRIMER_DIST)) {
    for (const r of parseRules(css)) {
      for (const sel of splitSelectors(r.selector)) {
        if (drawsLeftBar(sel, r.body)) bars.push({ selector: sel, file: path.relative(PRIMER_DIST, file) });
      }
    }
  }
  return bars;
}

/** Hiding rules in primer.css: `display: none` on a pseudo-element. */
function hidingSelectors(css: string): string[] {
  return parseRules(css)
    .filter((r) => parseDeclarations(r.body).some(([n, v]) => n === "display" && v === "none"))
    .flatMap((r) => splitSelectors(r.selector));
}

function covers(hide: string, bar: Bar): boolean {
  if (!/::after\s*$/.test(hide)) return false;
  const components = componentClasses(bar.selector);
  if (components.length === 0) return false;
  if (!components.every((c) => hide.includes(c))) return false;
  const hideState = new Set(stateTokens(hide));
  if (!stateTokens(bar.selector).every((t) => hideState.has(t))) return false;
  return cmp(specificity(hide), specificity(bar.selector)) > 0;
}

describe("Primer left accent bars", () => {
  const bars = findBars();
  const hides = hidingSelectors(primerCss);

  it("finds the bar rules in the installed CSS", () => {
    // Without this an unchanged finder that matches nothing would pass every
    // other assertion in the file.
    expect(bars.length).toBeGreaterThanOrEqual(1);
  });

  it("finds the bars of the components this app plans to use", () => {
    const text = bars.map((b) => b.selector).join("\n");
    expect(text).toContain("ActionList-ActionListItem");
    expect(text).toContain("FilteredActionList-ActionListItem");
    expect(text).toContain("TreeView-TreeViewItem");
    expect(text).toContain("UnderlineNav-OverflowMenuItem");
  });

  it("finder recognises a bar and ignores an underline", () => {
    expect(
      drawsLeftBar(
        ".x:after",
        'content:"";position:absolute;width:.25rem;left:calc(var(--base-size-8,.5rem)*-1);top:0',
      ),
    ).toBe(true);
    expect(
      drawsLeftBar(
        ".x:after",
        'content:"";position:absolute;height:2px;inset:auto 0 0;margin-bottom:0',
      ),
    ).toBe(false);
    expect(drawsLeftBar(".x", 'content:"";position:absolute;width:.25rem;left:0')).toBe(false);
  });

  it.each(bars.map((b) => [b.selector, b] as const))("%s is hidden", (_selector, bar) => {
    expect(
      hides.some((h) => covers(h, bar)),
      `no rule in src/primer.css hides the bar drawn by ${bar.selector} (${bar.file})`,
    ).toBe(true);
  });

  it("has no hiding rule that matches nothing", () => {
    for (const h of hides) {
      expect(
        bars.some((b) => covers(h, b)),
        `${h} hides a bar Primer no longer draws`,
      ).toBe(true);
    }
  });
});
