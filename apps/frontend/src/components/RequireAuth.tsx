import { useEffect, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuthStore } from "@/stores/authStore";

interface RequireAuthProps {
  children: ReactNode;
}

/**
 * Route guard — Phase 1 PR #6 task 1.7.
 *
 * Status machine (driven by `useAuthStore`):
 *   - `idle`           → kick off bootstrap (`/auth/me`) and show splash.
 *   - `bootstrapping`  → show splash (no flicker; refresh-survives-auth).
 *   - `authenticated`  → render children.
 *   - `anonymous`      → redirect to /login, preserving the original target.
 */
export function RequireAuth({ children }: RequireAuthProps) {
  const status = useAuthStore((s) => s.status);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const location = useLocation();

  useEffect(() => {
    if (status === "idle") {
      void bootstrap();
    }
  }, [status, bootstrap]);

  if (status === "idle" || status === "bootstrapping") {
    return (
      <div
        data-testid="auth-bootstrap-splash"
        className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground"
        aria-busy="true"
        aria-live="polite"
      >
        <span className="sr-only">Loading…</span>
      </div>
    );
  }

  if (status === "anonymous") {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname + location.search }}
      />
    );
  }

  return <>{children}</>;
}
