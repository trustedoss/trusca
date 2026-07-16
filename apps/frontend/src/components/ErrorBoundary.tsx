import { Component, type ErrorInfo, type ReactNode } from "react";

import i18n from "@/lib/i18n";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Phase 6 PR #19 — Top-level React Error Boundary.
 *
 * Catches render-time exceptions anywhere below it so a single broken
 * component cannot blank the entire app. The `fallback` prop lets each
 * call site provide a domain-specific message (e.g. "Components tab
 * failed to load") while the default surfaces a global retry hint.
 *
 * We intentionally do NOT auto-recover — a stale render path that throws
 * once will throw again on every render until the user navigates / reloads.
 * The fallback shows the error name + stack-frame snippet so the operator
 * can report it; full stack traces are sent to the browser console only.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Best-effort console reporting. In production we'd ship this to a
    // crash reporter (Sentry et al.) — that hook lives in Phase 8 PR #24.
    console.error("[ErrorBoundary] caught:", error, errorInfo.componentStack);
  }

  handleReload = (): void => {
    // Hard reload — the simplest way to get back to a known-good state
    // when a render path is poisoned.
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.hasError) {
      if (this.props.fallback !== undefined) {
        return this.props.fallback;
      }
      const errorName = this.state.error?.name ?? "Error";
      const errorMessage = this.state.error?.message ?? "Unknown error";
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
            {i18n.t("common:errors.boundary_title")}
          </h2>
          <p className="mt-2 text-sm text-muted-foreground">
            <code className="font-mono">{errorName}</code>: {errorMessage}
          </p>
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
