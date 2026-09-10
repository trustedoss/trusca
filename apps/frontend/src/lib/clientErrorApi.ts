// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Frontend crash-report client (#423).
 *
 * POST /v1/client-errors: unauthenticated (a crash can happen before there
 * is a token to attach; `api`'s interceptor still attaches one when a
 * session exists), fire-and-forget. `ErrorBoundary` is the one caller, and
 * it must never let a failed report turn one crash into a second visible
 * failure, so this swallows its own errors rather than returning a Promise
 * the caller has to remember to catch.
 */
import { api } from "@/lib/api";

export interface ClientErrorReport {
  message: string;
  stack?: string | undefined;
  componentStack?: string | undefined;
}

export function reportClientError(report: ClientErrorReport): void {
  void api
    .post("/v1/client-errors", {
      message: report.message.slice(0, 2000),
      stack: report.stack?.slice(0, 8000),
      component_stack: report.componentStack?.slice(0, 8000),
      url: window.location.href,
    })
    .catch(() => {
      // Best-effort. The user is already looking at the crash fallback;
      // a reporting failure has nothing more useful to do than stay silent.
    });
}
