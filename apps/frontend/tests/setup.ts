import "@testing-library/jest-dom/vitest";

import { afterEach, beforeAll } from "vitest";
import { cleanup } from "@testing-library/react";

import i18n from "@/lib/i18n";

beforeAll(() => {
  // Radix popper-based primitives (DropdownMenu — backs the new MultiSelect,
  // ReleaseSwitcher, VEX menus) call a few DOM APIs that jsdom does not
  // implement. Polyfill them once globally so any test that opens a Radix
  // dropdown works without repeating the stubs per file.
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.setPointerCapture) {
    Element.prototype.setPointerCapture = () => {};
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
  // Node 22+ exposes an experimental `localStorage` global that shadows
  // jsdom's `window.localStorage` and ends up as an empty plain object with
  // no `setItem`/`getItem`/`clear` methods. The ColumnsPicker (W9 #52)
  // persists user column-visibility choices via localStorage and its unit
  // tests round-trip those writes; replace the broken global with a real
  // Storage-shaped in-memory shim so any test that touches localStorage
  // sees a working API. The shim is per-process (jsdom is fresh per run).
  const ls = window.localStorage as unknown as { setItem?: unknown };
  if (typeof ls.setItem !== "function") {
    const store = new Map<string, string>();
    const storage: Storage = {
      get length() {
        return store.size;
      },
      clear: () => store.clear(),
      getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
      key: (index: number) => Array.from(store.keys())[index] ?? null,
      removeItem: (key: string) => {
        store.delete(key);
      },
      setItem: (key: string, value: string) => {
        store.set(key, String(value));
      },
    };
    Object.defineProperty(window, "localStorage", {
      value: storage,
      configurable: true,
      writable: false,
    });
  }
});

afterEach(() => {
  cleanup();
  // Reset to default language so a test that flips i18n state can't leak.
  void i18n.changeLanguage("en");
});
