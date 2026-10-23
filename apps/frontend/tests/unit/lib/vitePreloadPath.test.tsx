/**
 * The chunk-reload path through Vite's real preload helper.
 *
 * A synthetic `vite:preloadError` event is not enough: what matters is what
 * Vite does after our handler calls preventDefault. Its helper then resolves
 * the import to `undefined`, so the router's
 * `.then((m) => ({ default: m.Page }))` throws a TypeError that does not
 * read as a chunk error. Without the pending-reload flag that TypeError
 * reached ErrorBoundary as an ordinary crash: a flash of the generic
 * fallback and one false client-error report per tab per deploy.
 *
 * The helper is taken from the installed Vite build itself (the function the
 * build injects as `__vitePreload`), so a Vite upgrade that changes this
 * behaviour, or moves the helper, fails here rather than in production.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import { Suspense, lazy } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import * as clientErrorApi from "@/lib/clientErrorApi";
import {
  CHUNK_RELOAD_STORAGE_KEY,
  installChunkReloadHandler,
  resetChunkReloadStateForTests,
} from "@/lib/chunkReload";

vi.mock("@/lib/clientErrorApi", async (importOriginal) => {
  const actual = await importOriginal<typeof clientErrorApi>();
  return { ...actual, reportClientError: vi.fn() };
});

const mockedReport = vi.mocked(clientErrorApi.reportClientError);

type Preload = (
  baseModule: () => Promise<unknown>,
  deps?: string[],
  importerUrl?: string,
) => Promise<unknown>;

function loadVitePreload(): Preload {
  const source = readFileSync(
    path.resolve(process.cwd(), "node_modules/vite/dist/node/chunks/config.js"),
    "utf8",
  );
  const start = source.indexOf("function preload(baseModule, deps, importerUrl) {");
  const end = source.indexOf("\nfunction getPreloadCode", start);
  if (start < 0 || end < 0) {
    throw new Error("Vite's preload helper moved; update this test");
  }
  const body = source.slice(start, end);
  // The same free variables the build defines around the helper.
  return new Function(
    "__VITE_IS_MODERN__",
    "seen",
    "assetsURL",
    "scriptRel",
    `${body}\nreturn preload;`,
  )(true, {}, (dep: string) => dep, "modulepreload") as Preload;
}

const vitePreload = loadVitePreload();

function lazyMissingChunk() {
  // What the build emits for `lazy(() => import("./Page").then(...))` when
  // the chunk file is gone after a deploy.
  return lazy(() =>
    vitePreload(() =>
      Promise.reject(
        new TypeError(
          "Failed to fetch dynamically imported module: https://x/assets/Page-abc.js",
        ),
      ),
    ).then((m) => ({ default: (m as { Page: () => JSX.Element }).Page })),
  );
}

const originalLocation = window.location;
let reloadSpy: ReturnType<typeof vi.fn>;
let uninstall: () => void;
let consoleSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  window.sessionStorage.clear();
  resetChunkReloadStateForTests();
  mockedReport.mockClear();
  reloadSpy = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, reload: reloadSpy },
  });
  consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  uninstall = installChunkReloadHandler();
});

afterEach(() => {
  uninstall();
  resetChunkReloadStateForTests();
  consoleSpy.mockRestore();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

function renderRoute() {
  const Page = lazyMissingChunk();
  render(
    <ErrorBoundary>
      <Suspense fallback={<div data-testid="loading" />}>
        <Page />
      </Suspense>
    </ErrorBoundary>,
  );
}

describe("missing chunk through Vite's preload helper", () => {
  it("reloads once and neither shows nor reports the follow-on TypeError", async () => {
    renderRoute();

    await waitFor(() => expect(screen.queryByTestId("loading")).toBeNull());
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("error-boundary-fallback")).toBeNull();
    expect(mockedReport).not.toHaveBeenCalled();
  });

  it("shows and reports the failure when the reload already happened", async () => {
    window.sessionStorage.setItem(CHUNK_RELOAD_STORAGE_KEY, String(Date.now()));

    renderRoute();

    // Not prevented this time, so Vite rethrows the original import error.
    expect(
      await screen.findByText("This page could not be loaded"),
    ).toBeInTheDocument();
    expect(reloadSpy).not.toHaveBeenCalled();
    expect(mockedReport).toHaveBeenCalledTimes(1);
  });
});
