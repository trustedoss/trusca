// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { Component, type ErrorInfo, type ReactNode } from "react";

import { isChunkLoadError, reloadOnceForChunkError } from "@/lib/chunkReload";
import { reportClientError } from "@/lib/clientErrorApi";
import i18n from "@/lib/i18n";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  /**
   * When this value changes the boundary clears its error and renders the
   * children again. The route boundary in AppShell passes the pathname so
   * navigating away from a crashed page recovers without a reload.
   */
  resetKey?: unknown;
}

interface State {
  hasError: boolean;
  error: Error | null;
  /** A chunk-load failure triggered a page reload that is now under way. */
  reloading: boolean;
}

/**
 * Phase 6 PR #19 — Top-level React Error Boundary.
 *
 * Catches render-time exceptions anywhere below it so a single broken
 * component cannot blank the entire app. The `fallback` prop lets each
 * call site provide a domain-specific message (e.g. "Components tab
 * failed to load") while the default surfaces a global retry hint.
 *
 * We intentionally do NOT auto-recover from a render error: a stale render
 * path that throws once will throw again on every render. Two exceptions:
 * a change of `resetKey` (navigation) clears the error, and a failed lazy
 * chunk import triggers one page reload (see lib/chunkReload.ts).
 * The fallback shows a translated message; the raw error name and message
 * sit in a collapsed details block so an operator can still report it.
 * Full stack traces go to the browser console AND to
 * `POST /v1/client-errors` (#423), so a crash nobody is watching a console
 * for still lands somewhere an operator can find it.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null, reloading: false };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidUpdate(prevProps: Props): void {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, error: null, reloading: false });
    }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    if (isChunkLoadError(error) && reloadOnceForChunkError()) {
      // A deploy replaced the chunk files; the reload picks up the new ones.
      // Not reported: this is expected after every release.
      this.setState({ reloading: true });
      return;
    }
    console.error("[ErrorBoundary] caught:", error, errorInfo.componentStack);
    reportClientError({
      message: error.message,
      stack: error.stack,
      componentStack: errorInfo.componentStack ?? undefined,
    });
  }

  handleReload = (): void => {
    // Hard reload — the simplest way to get back to a known-good state
    // when a render path is poisoned.
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.state.reloading) {
        return null;
      }
      if (this.props.fallback !== undefined) {
        return this.props.fallback;
      }
      const errorName = this.state.error?.name ?? "Error";
      const errorMessage = this.state.error?.message ?? "";
      const chunkFailure = isChunkLoadError(this.state.error);
      return (
        <div
          data-testid="error-boundary-fallback"
          // A crash replaces the entire subtree — announce it assertively so
          // screen-reader users are not left on a silently swapped page.
          role="alert"
          className="mx-auto mt-16 max-w-lg rounded-md border border-destructive/30 bg-destructive/5 p-6"
        >
          <h2 className="text-lg font-semibold text-destructive">
            {/* Class component — use the i18n instance directly. The crash
                screen does not live-switch language (a reload follows anyway). */}
            {i18n.t(
              chunkFailure
                ? "common:errors.boundary_chunk_title"
                : "common:errors.boundary_title",
            )}
          </h2>
          <p
            className="mt-2 text-sm text-muted-foreground"
            data-testid="error-boundary-description"
          >
            {i18n.t(
              chunkFailure
                ? "common:errors.boundary_chunk_description"
                : "common:errors.boundary_description",
            )}
          </p>
          <details className="mt-3 text-xs text-muted-foreground">
            <summary className="cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {i18n.t("common:errors.boundary_details")}
            </summary>
            <p className="mt-1 break-words" data-testid="error-boundary-details">
              <code className="font-mono">{errorName}</code>: {errorMessage}
            </p>
          </details>
          <button
            type="button"
            onClick={this.handleReload}
            data-testid="error-boundary-reload"
            // W11-F polish — the error-boundary fallback is rendered before
            // shadcn/Button can be trusted (we may be in a render-failure
            // state). Inline the same motion + focus-ring vocabulary so it
            // still feels coherent if the user ends up here.
            className="mt-4 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors duration-fast ease-out-soft hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            {i18n.t("common:errors.boundary_reload")}
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
