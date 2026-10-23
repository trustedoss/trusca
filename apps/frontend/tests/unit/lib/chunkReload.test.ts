import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CHUNK_RELOAD_STORAGE_KEY,
  RELOAD_WINDOW_MS,
  installChunkReloadHandler,
  isChunkLoadError,
  reloadOnceForChunkError,
} from "@/lib/chunkReload";

const originalLocation = window.location;
let reloadSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  window.sessionStorage.clear();
  reloadSpy = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, reload: reloadSpy },
  });
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
  vi.restoreAllMocks();
});

describe("isChunkLoadError", () => {
  it.each([
    "Failed to fetch dynamically imported module: https://x/assets/a-1.js",
    "error loading dynamically imported module",
    "Importing a module script failed.",
    "Unable to preload CSS for /assets/b-2.css",
  ])("recognises %s", (message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true);
  });

  it("recognises a ChunkLoadError by name", () => {
    const err = new Error("whatever");
    err.name = "ChunkLoadError";
    expect(isChunkLoadError(err)).toBe(true);
  });

  it("ignores ordinary errors and non-errors", () => {
    expect(isChunkLoadError(new Error("kaboom"))).toBe(false);
    expect(isChunkLoadError("Failed to fetch dynamically imported module")).toBe(
      false,
    );
    expect(isChunkLoadError(null)).toBe(false);
  });
});

describe("reloadOnceForChunkError", () => {
  it("reloads the first time and records when", () => {
    expect(reloadOnceForChunkError(1_000_000)).toBe(true);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(window.sessionStorage.getItem(CHUNK_RELOAD_STORAGE_KEY)).toBe(
      "1000000",
    );
  });

  it("does not reload a second time inside the window (loop guard)", () => {
    expect(reloadOnceForChunkError(1_000_000)).toBe(true);
    expect(reloadOnceForChunkError(1_000_000 + RELOAD_WINDOW_MS - 1)).toBe(false);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it("reloads again once the window has passed (a later deploy)", () => {
    expect(reloadOnceForChunkError(1_000_000)).toBe(true);
    expect(reloadOnceForChunkError(1_000_000 + RELOAD_WINDOW_MS)).toBe(true);
    expect(reloadSpy).toHaveBeenCalledTimes(2);
  });

  it("does not reload when sessionStorage cannot record the attempt", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(reloadOnceForChunkError(1_000_000)).toBe(false);
    expect(reloadSpy).not.toHaveBeenCalled();
  });

  it("treats an unreadable storage value as no previous reload", () => {
    window.sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, "not-a-number");
    expect(reloadOnceForChunkError(1_000_000)).toBe(true);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });
});

describe("installChunkReloadHandler", () => {
  it("reloads on vite:preloadError and suppresses Vite's rethrow", () => {
    const uninstall = installChunkReloadHandler();
    const event = new Event("vite:preloadError", { cancelable: true });
    window.dispatchEvent(event);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
    uninstall();
  });

  it("lets the error propagate when the reload is suppressed", () => {
    const uninstall = installChunkReloadHandler();
    window.dispatchEvent(new Event("vite:preloadError", { cancelable: true }));
    const second = new Event("vite:preloadError", { cancelable: true });
    window.dispatchEvent(second);
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(second.defaultPrevented).toBe(false);
    uninstall();
  });

  it("stops listening after uninstall", () => {
    installChunkReloadHandler()();
    window.dispatchEvent(new Event("vite:preloadError", { cancelable: true }));
    expect(reloadSpy).not.toHaveBeenCalled();
  });
});
