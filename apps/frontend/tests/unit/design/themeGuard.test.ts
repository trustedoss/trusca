/**
 * W18 — the inline theme script and `src/lib/theme.ts` must agree.
 *
 * `index.html` resolves the theme before the bundle loads, so the first frame
 * is not a white flash for dark users. An inline script cannot import, so the
 * storage key and the class name exist twice. That is a deliberate trade (see
 * the note in `lib/theme.ts`) and it is only safe with this test: rename the
 * key in the module, and without a check here the app would keep working
 * while every reload silently reverted to the OS default — a bug nobody
 * reports because the app is merely "not remembering", which reads as a
 * preference not having been saved.
 *
 * The assertions read the real `index.html`. Asserting against a copy of the
 * script would be asserting that our copy matches our copy.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { DARK_CLASS, THEME_STORAGE_KEY } from "../../../src/lib/theme";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const HTML_PATH = path.join(__dirname, "..", "..", "..", "index.html");
const html = fs.readFileSync(HTML_PATH, "utf8");

/** The inline script, isolated so a match elsewhere in the file cannot pass. */
const inlineScript = (() => {
  const match = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!match) throw new Error("index.html no longer has an inline <script>");
  return match[1];
})();

describe("theme bootstrap", () => {
  it("reads the same storage key the module writes", () => {
    expect(inlineScript).toContain(`"${THEME_STORAGE_KEY}"`);
  });

  it("adds the same class Tailwind's dark variant looks for", () => {
    expect(inlineScript).toContain(`classList.add("${DARK_CLASS}")`);
  });

  it("treats a stored light preference as an override of a dark OS", () => {
    // The one branch that is easy to get backwards, and the one whose failure
    // is invisible in light-on-light development: a user who chose light on a
    // dark machine would get a dark first frame that React then corrects.
    expect(inlineScript).toContain('stored !== "light"');
  });

  it("survives storage being unavailable", () => {
    // Safari private browsing throws on localStorage access rather than
    // returning null. An exception here runs before the bundle, so it would
    // take the whole page down, not just the theme.
    expect(inlineScript).toMatch(/try\s*\{/);
    expect(inlineScript).toMatch(/catch/);
  });

  it("runs before the module bundle", () => {
    // Ordering is the entire point. If the bundle's <script> came first, the
    // browser would fetch and evaluate it before this ran.
    const inlineAt = html.indexOf("<script>");
    const bundleAt = html.indexOf('<script type="module"');
    expect(inlineAt).toBeGreaterThan(-1);
    expect(bundleAt).toBeGreaterThan(-1);
    expect(inlineAt).toBeLessThan(bundleAt);
  });
});
