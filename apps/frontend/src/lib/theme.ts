// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Theme resolution (W18).
 *
 * Why this is not in `uiStore`
 * ---------------------------
 * The other chrome preferences can wait for React. This one cannot: if the
 * class lands in an effect, the first painted frame is light and the user
 * watches the app flash white on every reload. So the preference is read by
 * an inline script in `index.html`, before the bundle is even requested.
 *
 * That script cannot import this module, which means the storage key and the
 * class name are the one piece of duplicated knowledge in the theme. They are
 * duplicated as *literals* — a plain string under a plain key — rather than as
 * logic, precisely so the inline copy stays three lines long. Putting the
 * preference in the zustand store would have made the inline script parse
 * zustand's `{"state":…,"version":…}` envelope, which is a shape we do not
 * control and would break silently on a library upgrade.
 *
 * `themeGuard.test.ts` holds the inline script and this module to the same
 * key and class, so the duplication cannot drift.
 */

/** What the user asked for. `system` defers to the OS. */
export type ThemePreference = "light" | "dark" | "system";

/** What actually gets painted. */
export type ResolvedTheme = "light" | "dark";

/** Shared with the inline script in `index.html`. */
export const THEME_STORAGE_KEY = "trusca-theme";

/** Shared with the inline script in `index.html`, and with Tailwind's `darkMode: ["class"]`. */
export const DARK_CLASS = "dark";

/**
 * Primer reads its colours from three attributes on <html> instead of a class
 * (`data-color-mode`, `data-light-theme`, `data-dark-theme`). They are written
 * next to the `dark` class, in `applyTheme` and in the inline script of
 * `index.html`, so Primer and Tailwind never disagree. The theme names are the
 * two @primer/primitives themes `main.tsx` loads. `data-color-mode` carries the
 * resolved mode, never "auto".
 */
export const PRIMER_COLOR_MODE_ATTR = "data-color-mode";
export const PRIMER_LIGHT_THEME_ATTR = "data-light-theme";
export const PRIMER_DARK_THEME_ATTR = "data-dark-theme";
export const PRIMER_LIGHT_THEME = "light";
export const PRIMER_DARK_THEME = "dark";

const PREFERENCES: readonly ThemePreference[] = ["light", "dark", "system"];

function isPreference(value: unknown): value is ThemePreference {
  return (
    typeof value === "string" &&
    (PREFERENCES as readonly string[]).includes(value)
  );
}

/**
 * The stored preference, or `system` when there is none.
 *
 * `system` is the default rather than `light` so a user whose OS is dark gets
 * dark on first visit — the plan's "light 기본" means light is what an
 * undecided *system* resolves to, not that the product overrides a stated OS
 * preference.
 */
export function readStoredPreference(): ThemePreference {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isPreference(raw) ? raw : "system";
  } catch {
    // Private browsing modes throw on access rather than returning null.
    // A theme is not worth a crash.
    return "system";
  }
}

export function storePreference(preference: ThemePreference): void {
  try {
    if (preference === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    }
  } catch {
    // As above — the toggle still works for this session.
  }
}

/** Whether the OS asks for dark. Light when it has no opinion. */
export function systemPrefersDark(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

export function resolvePreference(preference: ThemePreference): ResolvedTheme {
  if (preference === "system") return systemPrefersDark() ? "dark" : "light";
  return preference;
}

/**
 * Put the resolved theme on the document element: the `dark` class and the
 * three Primer attributes together.
 *
 * Also updates `theme-color`, the strip of browser chrome around the page on
 * mobile. Leaving it fixed framed a dark app in a bar belonging to neither
 * theme.
 *
 * The value is read from `--topbar` rather than written here, so the browser
 * chrome continues the app's own chrome by construction — and so this file
 * holds no colour of its own. The first version hardcoded two hexes and the
 * token lint was right to stop it.
 */
export function applyTheme(theme: ResolvedTheme): void {
  const root = document.documentElement;
  root.classList.toggle(DARK_CLASS, theme === "dark");
  root.setAttribute(PRIMER_COLOR_MODE_ATTR, theme);
  root.setAttribute(PRIMER_LIGHT_THEME_ATTR, PRIMER_LIGHT_THEME);
  root.setAttribute(PRIMER_DARK_THEME_ATTR, PRIMER_DARK_THEME);
  root.style.colorScheme = theme;

  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  // Read after the class change above, so this picks up the theme just set.
  const bar = getComputedStyle(root).getPropertyValue("--topbar").trim();
  if (bar) meta.setAttribute("content", bar);
}

/** The mode <html> shows right now, read from the `dark` class. */
export function readDocumentTheme(root: HTMLElement = document.documentElement): ResolvedTheme {
  return root.classList.contains(DARK_CLASS) ? "dark" : "light";
}

/**
 * Call `onChange` when something flips the `dark` class on <html>. Theme state
 * is a module variable, not React state, so the Primer provider follows the
 * element instead. Returns the unsubscribe function.
 */
export function watchDocumentTheme(
  onChange: (theme: ResolvedTheme) => void,
  root: HTMLElement = document.documentElement,
): () => void {
  let last = readDocumentTheme(root);
  const observer = new MutationObserver(() => {
    const next = readDocumentTheme(root);
    if (next !== last) {
      last = next;
      onChange(next);
    }
  });
  observer.observe(root, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}
