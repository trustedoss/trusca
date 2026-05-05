import { create } from "zustand";

/**
 * Auth store — Phase 1 PR #6 task 1.7.
 *
 * Holds the **in-memory** access token plus a coarse `status` machine that
 * RequireAuth uses to decide between splash / redirect / render.
 *
 * Hard rules (CLAUDE.md §3 + 1.7 brief):
 *   - Access token lives only in this store. Never localStorage / cookie.
 *   - Refresh token is an HttpOnly cookie scoped to `/auth` — managed by
 *     the backend; this store never touches it.
 *   - The store does **not** import the router. `logout()` clears state and
 *     emits an `auth:expired` window event; the router-aware listener (in
 *     AppProviders) handles navigation. This keeps the store testable without
 *     a router and side-steps circular imports.
 *   - `isAuthenticated` is derived (`status === "authenticated"`) but kept as
 *     a real field so harness/external code can read it via getter without
 *     subscribing.
 */

export type AuthRole = "super_admin" | "team_admin" | "developer";

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  role: AuthRole;
  /**
   * Backend currently returns `is_active` / `is_superuser`; surfaced here so
   * an Admin layout can branch on superuser without another fetch.
   */
  isActive: boolean;
  isSuperuser: boolean;
  /**
   * Team association is not yet returned by the v1 /auth/me endpoint, but
   * downstream consumers reference it — keep the slot so we don't churn the
   * type when the backend lands the column.
   */
  teamId: string | null;
}

export type AuthStatus =
  | "idle"
  | "bootstrapping"
  | "authenticated"
  | "anonymous";

interface AuthState {
  user: AuthUser | null;
  /** In-memory only. Persisting this is a security regression. */
  accessToken: string | null;
  status: AuthStatus;
  /** Derived from `status === "authenticated"`. */
  isAuthenticated: boolean;
  setUser: (user: AuthUser | null) => void;
  setAccessToken: (token: string | null) => void;
  setStatus: (status: AuthStatus) => void;
  /**
   * Hydrate from the refresh cookie. Called once on app mount and on
   * `?bootstrap` events. Idempotent: re-running while authenticated is a
   * cheap no-op.
   */
  bootstrap: () => Promise<void>;
  /**
   * Revoke the refresh cookie (best-effort) and reset memory. Always returns
   * even if the network call fails — local state must clear.
   */
  logout: () => Promise<void>;
  reset: () => void;
}

const initialState = {
  user: null as AuthUser | null,
  accessToken: null as string | null,
  status: "idle" as AuthStatus,
  isAuthenticated: false,
};

export const useAuthStore = create<AuthState>((set, get) => ({
  ...initialState,
  setUser: (user) => set({ user }),
  setAccessToken: (accessToken) => set({ accessToken }),
  setStatus: (status) =>
    set({ status, isAuthenticated: status === "authenticated" }),
  reset: () => set({ ...initialState, status: "anonymous", isAuthenticated: false }),
  async bootstrap() {
    const { status } = get();
    if (status === "bootstrapping" || status === "authenticated") {
      return;
    }
    set({ status: "bootstrapping", isAuthenticated: false });
    // Lazy import to avoid a circular dependency: api.ts imports the store
    // (for the request interceptor), so the store can't import api.ts at
    // module-load time.
    const { fetchMe } = await import("@/lib/api");
    try {
      const me = await fetchMe();
      set({
        user: me,
        status: "authenticated",
        isAuthenticated: true,
      });
    } catch {
      // 401 here means no valid refresh cookie — treat as anonymous. Any
      // other error (transport, 5xx) also lands as anonymous so the user can
      // re-authenticate manually instead of seeing a stuck splash.
      set({
        user: null,
        accessToken: null,
        status: "anonymous",
        isAuthenticated: false,
      });
    }
  },
  async logout() {
    try {
      const { postLogout } = await import("@/lib/api");
      await postLogout();
    } catch {
      // Ignore — we still want to clear local state.
    }
    set({ ...initialState, status: "anonymous", isAuthenticated: false });
  },
}));

/**
 * Convenience getter for non-React code paths (axios interceptor) that need
 * the latest token without subscribing.
 */
export function getAccessToken(): string | null {
  return useAuthStore.getState().accessToken;
}
