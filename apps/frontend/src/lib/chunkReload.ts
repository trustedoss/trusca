// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors

/**
 * Recovery from failed lazy-route chunk loads.
 *
 * After a redeploy the hashed chunk files the running tab knows about are
 * gone from the server, so the next `React.lazy()` import rejects with
 * "Failed to fetch dynamically imported module". A full reload fetches the
 * new index.html and its new chunk names, which is the fix in almost every
 * case. If the reload does not help (the server is down, a proxy strips the
 * assets), reloading again would loop forever, so a reload is allowed only
 * once per RELOAD_WINDOW_MS, tracked in sessionStorage.
 */

export const CHUNK_RELOAD_STORAGE_KEY = "trusca:chunk-reload-at";

/** A second reload inside this window is treated as a loop and skipped. */
export const RELOAD_WINDOW_MS = 60_000;

// Chromium, Firefox and Safari word the dynamic import rejection
// differently; Vite's own preload helper reports CSS failures as
// "Unable to preload CSS". Safari's "Importing a module script failed." is
// also what it reports when a module that DID load throws while evaluating,
// so a genuine bug can match here. That costs one reload; the second
// occurrence inside RELOAD_WINDOW_MS is not reloaded, and ErrorBoundary then
// reports it like any other crash.
const CHUNK_ERROR_PATTERN =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS/i;

export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  if (error.name === "ChunkLoadError") return true;
  // problem-detail-lint-allow: matched against browser wording only, never rendered
  return CHUNK_ERROR_PATTERN.test(error.message);
}

function readLastReload(): number | null {
  try {
    const raw = window.sessionStorage.getItem(CHUNK_RELOAD_STORAGE_KEY);
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

function writeLastReload(now: number): boolean {
  try {
    window.sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, String(now));
    return true;
  } catch {
    // Storage blocked (private mode, quota): without a place to record the
    // attempt we cannot guard against a loop, so do not reload at all.
    return false;
  }
}

/**
 * Set once a reload has been started from this page. Everything that fails
 * after that point is fallout of the same stale build: with preventDefault
 * on `vite:preloadError`, Vite's preload helper resolves the import to
 * `undefined`, so the route's `.then((m) => ({ default: m.Page }))` throws a
 * TypeError that does not look like a chunk error. ErrorBoundary checks this
 * flag so it neither shows the fallback nor reports that TypeError.
 */
let reloadPending = false;

export function isReloadPending(): boolean {
  return reloadPending;
}

/** Test hook: a real page never comes back from a started reload. */
export function resetChunkReloadStateForTests(): void {
  reloadPending = false;
}

/**
 * Reload the page once for a chunk-load failure.
 *
 * Returns true when a reload was started, false when it was suppressed
 * because one already happened inside the window (or storage is
 * unavailable). Callers show an error message when this returns false.
 */
export function reloadOnceForChunkError(now: number = Date.now()): boolean {
  const last = readLastReload();
  if (last !== null && now - last >= 0 && now - last < RELOAD_WINDOW_MS) {
    return false;
  }
  if (!writeLastReload(now)) return false;
  reloadPending = true;
  window.location.reload();
  return true;
}

/**
 * Listen for Vite's `vite:preloadError`, fired when a preloaded chunk or
 * stylesheet fails. Calling preventDefault stops Vite from rethrowing, which
 * we only want when a reload is actually under way; otherwise the error
 * propagates to the route ErrorBoundary and the user sees a message. When it
 * is prevented, the import resolves to `undefined` and the caller throws a
 * TypeError; see `reloadPending` for how that is kept quiet.
 */
export function installChunkReloadHandler(target: Window = window): () => void {
  const handler = (event: Event) => {
    if (reloadOnceForChunkError()) {
      event.preventDefault();
    }
  };
  target.addEventListener("vite:preloadError", handler);
  return () => target.removeEventListener("vite:preloadError", handler);
}
