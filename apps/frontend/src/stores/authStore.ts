import { create } from "zustand";

/**
 * Phase 0 placeholder. Real authentication wiring (FastAPI-Users JWT,
 * refresh-token rotation, axios interceptor) lands in PR #5 / Phase 1.
 *
 * Type names are deliberately stable so downstream consumers can import them
 * now without reaching into implementation details.
 */
export type AuthRole = "super_admin" | "team_admin" | "developer";

export interface AuthUser {
  id: string;
  email: string;
  displayName: string;
  role: AuthRole;
  teamId: string | null;
}

interface AuthState {
  user: AuthUser | null;
  accessToken: string | null;
  isAuthenticated: boolean;
  setUser: (user: AuthUser | null) => void;
  setAccessToken: (token: string | null) => void;
  reset: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  isAuthenticated: false,
  setUser: (user) => set({ user, isAuthenticated: user !== null }),
  setAccessToken: (accessToken) => set({ accessToken }),
  reset: () =>
    set({ user: null, accessToken: null, isAuthenticated: false }),
}));
