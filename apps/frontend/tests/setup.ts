import "@testing-library/jest-dom/vitest";

import { afterEach, beforeAll, expect } from "vitest";
import { cleanup } from "@testing-library/react";

import i18n from "@/lib/i18n";

// ---------------------------------------------------------------------------
// Network guard (issue #267)
// ---------------------------------------------------------------------------
// The CI suite flaked twice with no repro: a different test failed each
// time, once with `AggregateError`/`emitErrorEvent` noise that looked like a
// real socket call escaping a mock. Nothing here ever blocked `fetch` or
// `XMLHttpRequest`, so a request that slipped past a mock silently went to
// the network (or hung waiting on a host that isn't there in CI) instead of
// failing the test that made the call, and the failure then surfaced later, in
// whichever test happened to be running when the stray response/error
// landed. Replacing both with a stub that throws immediately, naming the
// call site and the current test, turns that class of leak into a
// deterministic, attributable failure instead of cross-test noise.
let networkAllowedForCurrentTest = false;

/**
 * Opt-in escape hatch for a test that intentionally exercises a real
 * `fetch`/`XMLHttpRequest` call (e.g. asserting on jsdom's own network-error
 * shape). Call it from inside the test body; the guard re-arms itself in
 * `afterEach` below so the opt-out can never leak into the next test.
 */
export function allowNetworkInThisTest(): void {
  networkAllowedForCurrentTest = true;
}

function currentTestName(): string {
  return expect.getState().currentTestName ?? "(unknown test)";
}

function urlFromFetchArgs(args: Parameters<typeof fetch>): string {
  const [input] = args;
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.href;
  return (input as Request).url;
}

function networkGuardError(surface: string, url: string): Error {
  return new Error(
    `Unmocked ${surface} call to "${url}" during test "${currentTestName()}". ` +
      "Unit tests must not perform real network I/O; mock this request, or " +
      "call allowNetworkInThisTest() from within the test if it intentionally " +
      "needs the real implementation.",
  );
}

beforeAll(() => {
  const realFetch = globalThis.fetch?.bind(globalThis);
  globalThis.fetch = ((...args: Parameters<typeof fetch>) => {
    if (networkAllowedForCurrentTest) {
      if (!realFetch) {
        throw new Error(
          "allowNetworkInThisTest() was called but no real fetch implementation " +
            "is available in this environment.",
        );
      }
      return realFetch(...args);
    }
    throw networkGuardError("fetch", urlFromFetchArgs(args));
  }) as typeof fetch;

  // jsdom ships a real `XMLHttpRequest`, and axios (our main API client)
  // picks the XHR adapter in a browser-like environment, so a mock that only
  // stubs `fetch` would still let an axios call reach the network.
  const RealXMLHttpRequest = globalThis.XMLHttpRequest;
  if (RealXMLHttpRequest) {
    const originalOpen = RealXMLHttpRequest.prototype.open;
    RealXMLHttpRequest.prototype.open = function guardedOpen(
      this: XMLHttpRequest,
      method: string,
      url: string | URL,
      ...rest: unknown[]
    ) {
      if (!networkAllowedForCurrentTest) {
        throw networkGuardError("XMLHttpRequest", String(url));
      }
      return (
        originalOpen as unknown as (
          this: XMLHttpRequest,
          ...openArgs: unknown[]
        ) => void
      ).call(this, method, url, ...rest);
    };
  }
});

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
  // cmdk (W9-#54 CommandMenu) calls ResizeObserver to measure its list panel.
  // jsdom does not ship it, so provide a no-op stub that matches the
  // ResizeObserver contract just enough for cmdk to mount.
  if (typeof window !== "undefined" && !window.ResizeObserver) {
    window.ResizeObserver = class {
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    } as unknown as typeof ResizeObserver;
  }
});

afterEach(() => {
  cleanup();
  // Reset to default language so a test that flips i18n state can't leak.
  void i18n.changeLanguage("en");
  // Re-arm the network guard so an `allowNetworkInThisTest()` opt-out never
  // survives into the next test.
  networkAllowedForCurrentTest = false;
});
