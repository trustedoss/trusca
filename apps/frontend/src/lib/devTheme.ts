/**
 * Dev-only AIS theme prototype switch (see src/styles/theme-ais.css).
 *
 * Deliberately NOT part of uiStore: the persisted store schema
 * (`trustedoss-ui`) stays untouched, and discarding the prototype is a
 * file deletion, not a store migration. State lives in its own
 * localStorage key and is mirrored onto `document.documentElement` so
 * portal surfaces (Dialog / Popover / Toast render into document.body)
 * pick the theme up too — a wrapper class on the app content would miss
 * them.
 *
 * Only imported from DEV-guarded code paths (main.tsx dev branch, the
 * dev toggle component), so production builds tree-shake the module.
 */

export const AIS_THEME_CLASS = "theme-ais";
export const AIS_STORAGE_KEY = "trusca-dev-theme-ais";

function readFlag(): boolean {
  try {
    return window.localStorage.getItem(AIS_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function isAisThemeEnabled(): boolean {
  return document.documentElement.classList.contains(AIS_THEME_CLASS);
}

export function setAisTheme(enabled: boolean): void {
  document.documentElement.classList.toggle(AIS_THEME_CLASS, enabled);
  try {
    if (enabled) {
      window.localStorage.setItem(AIS_STORAGE_KEY, "1");
    } else {
      window.localStorage.removeItem(AIS_STORAGE_KEY);
    }
  } catch {
    // Storage unavailable (private mode etc.) — theme still applies for
    // the current page, it just won't survive a reload.
  }
}

/** Restore the persisted flag on boot (any route, login included). */
export function initAisTheme(): void {
  document.documentElement.classList.toggle(AIS_THEME_CLASS, readFlag());
}
