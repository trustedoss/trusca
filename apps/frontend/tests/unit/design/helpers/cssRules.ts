/**
 * A small CSS reader for the design guards. It is not a CSS parser: it finds
 * innermost `selector { declarations }` blocks, which is all the guards need
 * for Primer's minified dist files and our own primer.css.
 */
import * as fs from "node:fs";
import * as path from "node:path";

export interface Rule {
  selector: string;
  body: string;
}

export function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Innermost blocks, with at-rule wrappers (`@media`) left out of the selector. */
export function parseRules(css: string): Rule[] {
  const rules: Rule[] = [];
  const rx = /([^{}]+)\{([^{}]*)\}/g;
  for (const m of stripCssComments(css).matchAll(rx)) {
    const selector = m[1].trim();
    if (selector.startsWith("@")) continue;
    rules.push({ selector, body: m[2] });
  }
  return rules;
}

/** Split `a, b:is(c, d)` on the commas that are not inside parentheses or brackets. */
export function splitSelectors(list: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let cur = "";
  for (const ch of list) {
    if (ch === "(" || ch === "[") depth++;
    if (ch === ")" || ch === "]") depth--;
    if (ch === "," && depth === 0) {
      out.push(cur.trim());
      cur = "";
    } else cur += ch;
  }
  if (cur.trim()) out.push(cur.trim());
  return out;
}

/** `name: value` pairs of a declaration block. */
export function parseDeclarations(body: string): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const part of splitTopLevel(body, ";")) {
    const i = part.indexOf(":");
    if (i < 0) continue;
    const name = part.slice(0, i).trim();
    const value = part.slice(i + 1).trim();
    if (name) out.push([name, value]);
  }
  return out;
}

function splitTopLevel(text: string, sep: string): string[] {
  const out: string[] = [];
  let depth = 0;
  let quote = "";
  let cur = "";
  for (const ch of text) {
    if (quote) {
      if (ch === quote) quote = "";
    } else if (ch === '"' || ch === "'") quote = ch;
    else if (ch === "(") depth++;
    else if (ch === ")") depth--;
    if (ch === sep && depth === 0 && !quote) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  if (cur.trim()) out.push(cur);
  return out;
}

/** Specificity as [ids, classes/attributes/pseudo-classes, elements/pseudo-elements]. */
export type Specificity = [number, number, number];

export function specificity(selector: string): Specificity {
  let s = selector;
  const total: Specificity = [0, 0, 0];
  const add = (x: Specificity) => {
    total[0] += x[0];
    total[1] += x[1];
    total[2] += x[2];
  };
  // Functional pseudo-classes: :where adds nothing, :not and :is add their most specific argument.
  for (;;) {
    const m = s.match(/:(where|not|is|has)\(/);
    if (!m || m.index === undefined) break;
    let depth = 1;
    let i = m.index + m[0].length;
    while (i < s.length && depth > 0) {
      if (s[i] === "(") depth++;
      if (s[i] === ")") depth--;
      i++;
    }
    const inner = s.slice(m.index + m[0].length, i - 1);
    if (m[1] !== "where") {
      const best = splitSelectors(inner)
        .map(specificity)
        .reduce<Specificity>((a, b) => (cmp(b, a) > 0 ? b : a), [0, 0, 0]);
      add(best);
    }
    s = s.slice(0, m.index) + s.slice(i);
  }
  s = s.replace(/"[^"]*"|'[^']*'/g, '""');
  total[0] += (s.match(/#[\w-]+/g) ?? []).length;
  const attrs = (s.match(/\[[^\]]*\]/g) ?? []).length;
  s = s.replace(/\[[^\]]*\]/g, "");
  total[1] += attrs + (s.match(/\.[\w-]+/g) ?? []).length;
  total[2] += (s.match(/::[\w-]+/g) ?? []).length;
  s = s.replace(/::[\w-]+/g, "");
  total[1] += (s.match(/:[\w-]+/g) ?? []).length;
  // Legacy single-colon pseudo-elements count as elements.
  const legacy = (s.match(/:(before|after|first-line|first-letter)\b/g) ?? []).length;
  total[1] -= legacy;
  total[2] += legacy;
  s = s.replace(/:[\w-]+/g, "").replace(/[.#][\w-]+/g, "");
  total[2] += (s.match(/(^|[\s>+~])[a-zA-Z][\w-]*/g) ?? []).length;
  return total;
}

export function cmp(a: Specificity, b: Specificity): number {
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i] - b[i];
  return 0;
}

export function readCssTree(dir: string): Array<{ file: string; css: string }> {
  const out: Array<{ file: string; css: string }> = [];
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...readCssTree(full));
    else if (e.name.endsWith(".css")) out.push({ file: full, css: fs.readFileSync(full, "utf8") });
  }
  return out;
}
