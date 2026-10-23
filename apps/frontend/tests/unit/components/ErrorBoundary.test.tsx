import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import {
  reloadOnceForChunkError,
  resetChunkReloadStateForTests,
} from "@/lib/chunkReload";

import * as clientErrorApi from "@/lib/clientErrorApi";
import { reportClientError } from "@/lib/clientErrorApi";

vi.mock("@/lib/clientErrorApi", async (importOriginal) => {
  const actual = await importOriginal<typeof clientErrorApi>();
  return { ...actual, reportClientError: vi.fn() };
});

const mockedReport = vi.mocked(reportClientError);

function Boom(): JSX.Element {
  throw new Error("kaboom");
}

function ChunkBoom(): JSX.Element {
  throw new TypeError(
    "Failed to fetch dynamically imported module: https://x/assets/Page-abc.js",
  );
}

describe("ErrorBoundary", () => {
  let consoleSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // React logs caught errors via console.error — silence to keep test
    // output readable.
    consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    mockedReport.mockClear();
  });
  afterEach(() => {
    consoleSpy.mockRestore();
  });

  it("renders children when no error is thrown", () => {
    render(
      <ErrorBoundary>
        <div data-testid="child">ok</div>
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("child")).toBeInTheDocument();
  });

  it("shows a translated message and keeps the raw error in details", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary-fallback")).toBeInTheDocument();
    expect(screen.getByTestId("error-boundary-description")).toHaveTextContent(
      "This part of the page could not be shown.",
    );
    expect(screen.getByTestId("error-boundary-description")).not.toHaveTextContent(
      "kaboom",
    );
    // The raw error stays available to an operator, inside <details>.
    const details = screen.getByTestId("error-boundary-details");
    expect(details.closest("details")).not.toBeNull();
    expect(details).toHaveTextContent("Error: kaboom");
    expect(screen.getByTestId("error-boundary-reload")).toBeInTheDocument();
  });

  it("announces the crash via role=alert and uses the i18n strings", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    const fallback = screen.getByTestId("error-boundary-fallback");
    // a11y: the swapped-in crash screen must be announced assertively.
    expect(fallback).toHaveAttribute("role", "alert");
    // i18n: the copy comes from common:errors.* (EN default in tests) —
    // no hardcoded English strings left in the component.
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByTestId("error-boundary-reload")).toHaveTextContent(
      "Reload page",
    );
  });

  it("reports the crash to the backend (#423)", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(mockedReport).toHaveBeenCalledTimes(1);
    const [report] = mockedReport.mock.calls[0]!;
    expect(report.message).toBe("kaboom");
    expect(report.stack).toContain("kaboom");
    expect(report.componentStack).toContain("Boom");
  });

  it("renders the provided custom fallback instead of the default", () => {
    render(
      <ErrorBoundary fallback={<div data-testid="custom-fallback">nope</div>}>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("custom-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("error-boundary-fallback")).toBeNull();
  });

  describe("chunk-load failures", () => {
    const originalLocation = window.location;
    let reloadSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      window.sessionStorage.clear();
      resetChunkReloadStateForTests();
      reloadSpy = vi.fn();
      Object.defineProperty(window, "location", {
        configurable: true,
        value: { ...originalLocation, reload: reloadSpy },
      });
    });
    afterEach(() => {
      resetChunkReloadStateForTests();
      Object.defineProperty(window, "location", {
        configurable: true,
        value: originalLocation,
      });
    });

    it("reloads once instead of showing the raw import error", () => {
      render(
        <ErrorBoundary>
          <ChunkBoom />
        </ErrorBoundary>,
      );
      expect(reloadSpy).toHaveBeenCalledTimes(1);
      // Expected after every deploy, so not reported as a crash.
      expect(mockedReport).not.toHaveBeenCalled();
      expect(screen.queryByTestId("error-boundary-fallback")).toBeNull();
    });

    it("shows the translated update message when a reload already happened", () => {
      window.sessionStorage.setItem(
        "trusca:chunk-reload-at",
        String(Date.now()),
      );
      render(
        <ErrorBoundary>
          <ChunkBoom />
        </ErrorBoundary>,
      );
      expect(reloadSpy).not.toHaveBeenCalled();
      expect(
        screen.getByText("This page could not be loaded"),
      ).toBeInTheDocument();
      expect(screen.getByTestId("error-boundary-description")).toHaveTextContent(
        "TRUSCA may have been updated since this tab was opened.",
      );
      expect(screen.getByTestId("error-boundary-description")).not.toHaveTextContent(
        "dynamically imported",
      );
      expect(mockedReport).toHaveBeenCalledTimes(1);
    });

    it("stays quiet about any error once a reload is under way", () => {
      // The TypeError from Vite's undefined module is not a chunk error by
      // its wording, but it is fallout of the reload already started.
      reloadOnceForChunkError();
      expect(reloadSpy).toHaveBeenCalledTimes(1);
      render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      );
      expect(screen.queryByTestId("error-boundary-fallback")).toBeNull();
      expect(mockedReport).not.toHaveBeenCalled();
      expect(reloadSpy).toHaveBeenCalledTimes(1);
    });

    it("reports Safari's module error when it recurs after the one reload", () => {
      // Safari words a module that threw while evaluating the same way as a
      // missing chunk. The first costs a reload; the repeat is a real crash.
      window.sessionStorage.setItem(
        "trusca:chunk-reload-at",
        String(Date.now()),
      );
      function SafariBoom(): JSX.Element {
        throw new TypeError("Importing a module script failed.");
      }
      render(
        <ErrorBoundary>
          <SafariBoom />
        </ErrorBoundary>,
      );
      expect(reloadSpy).not.toHaveBeenCalled();
      expect(mockedReport).toHaveBeenCalledTimes(1);
      expect(mockedReport.mock.calls[0]![0].message).toBe(
        "Importing a module script failed.",
      );
    });
  });
});
