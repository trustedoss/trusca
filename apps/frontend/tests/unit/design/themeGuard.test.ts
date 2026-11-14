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

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { initTheme, setThemePreference } from "../../../src/hooks/useTheme";
import {
  DARK_CLASS,
  PRIMER_COLOR_MODE_ATTR,
  PRIMER_DARK_THEME,
  PRIMER_DARK_THEME_ATTR,
  PRIMER_LIGHT_THEME,
  PRIMER_LIGHT_THEME_ATTR,
  THEME_STORAGE_KEY,
} from "../../../src/lib/theme";

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

/**
 * Primer colour mode. Primer reads three attributes on <html> where Tailwind
 * reads the `dark` class, so every path that sets the class must set the
 * attributes with it, or a Primer component paints the wrong theme next to a
 * correct Tailwind shell. Two writers exist (the inline script and
 * `applyTheme`); each is driven in all four states a user can be in.
 */
type State = {
  name: string;
  stored: string | null;
  osDark: boolean;
  expected: "light" | "dark";
};

const STATES: State[] = [
  { name: "light chosen on a dark OS", stored: "light", osDark: true, expected: "light" },
  { name: "dark chosen on a light OS", stored: "dark", osDark: false, expected: "dark" },
  { name: "system on a light OS", stored: null, osDark: false, expected: "light" },
  { name: "system on a dark OS", stored: null, osDark: true, expected: "dark" },
];

function attrsOf(el: HTMLElement) {
  return {
    mode: el.getAttribute("data-color-mode"),
    light: el.getAttribute("data-light-theme"),
    dark: el.getAttribute("data-dark-theme"),
    hasDarkClass: el.classList.contains(DARK_CLASS),
  };
}

function expectAgreement(el: HTMLElement, expected: "light" | "dark") {
  const a = attrsOf(el);
  expect(a.mode).toBe(expected);
  expect(a.hasDarkClass).toBe(expected === "dark");
  expect(a.light).toBe(PRIMER_LIGHT_THEME);
  expect(a.dark).toBe(PRIMER_DARK_THEME);
}

describe("Primer colour-mode attributes agree with the dark class", () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    const root = document.documentElement;
    root.classList.remove(DARK_CLASS);
    for (const n of ["data-color-mode", "data-light-theme", "data-dark-theme"]) {
      root.removeAttribute(n);
    }
    window.localStorage.clear();
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  function mockOs(dark: boolean) {
    window.matchMedia = ((query: string) => ({
      matches: dark && query.includes("dark"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia;
  }

  it.each(STATES)("inline script: $name", ({ stored, osDark, expected }) => {
    mockOs(osDark);
    if (stored) window.localStorage.setItem(THEME_STORAGE_KEY, stored);
    // The real script text from index.html, evaluated against jsdom.
    new Function(inlineScript)();
    expectAgreement(document.documentElement, expected);
  });

  it("inline script: unreadable storage still leaves a complete light set", () => {
    mockOs(true);
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    try {
      new Function(inlineScript)();
    } finally {
      spy.mockRestore();
    }
    expectAgreement(document.documentElement, "light");
  });

  it.each(STATES)("applyTheme via initTheme: $name", ({ stored, osDark, expected }) => {
    mockOs(osDark);
    if (stored) window.localStorage.setItem(THEME_STORAGE_KEY, stored);
    initTheme();
    expectAgreement(document.documentElement, expected);
  });

  it("applyTheme keeps them together when the user switches both ways", () => {
    mockOs(false);
    initTheme();
    expectAgreement(document.documentElement, "light");
    setThemePreference("dark");
    expectAgreement(document.documentElement, "dark");
    setThemePreference("light");
    expectAgreement(document.documentElement, "light");
    mockOs(true);
    setThemePreference("system");
    expectAgreement(document.documentElement, "dark");
  });

  it("the inline script and lib/theme.ts name the same attributes and themes", () => {
    for (const attr of [
      PRIMER_COLOR_MODE_ATTR,
      PRIMER_LIGHT_THEME_ATTR,
      PRIMER_DARK_THEME_ATTR,
    ]) {
      expect(inlineScript).toContain(`"${attr}"`);
    }
    expect(inlineScript).toContain(`"${PRIMER_LIGHT_THEME}"`);
    expect(inlineScript).toContain(`"${PRIMER_DARK_THEME}"`);
  });
});
