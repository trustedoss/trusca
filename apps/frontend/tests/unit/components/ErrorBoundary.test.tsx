import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "@/components/ErrorBoundary";

function Boom(): JSX.Element {
  throw new Error("kaboom");
}

describe("ErrorBoundary", () => {
  let consoleSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    // React logs caught errors via console.error — silence to keep test
    // output readable.
    consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
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

  it("renders default fallback with error name + message on render error", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("error-boundary-fallback")).toBeInTheDocument();
    expect(screen.getByText(/Error/)).toBeInTheDocument();
    expect(screen.getByText(/kaboom/)).toBeInTheDocument();
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

  it("renders the provided custom fallback instead of the default", () => {
    render(
      <ErrorBoundary fallback={<div data-testid="custom-fallback">nope</div>}>
        <Boom />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId("custom-fallback")).toBeInTheDocument();
    expect(screen.queryByTestId("error-boundary-fallback")).toBeNull();
  });
});
