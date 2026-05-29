import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * UI store — chrome-level preferences that survive a reload.
 *
 * Distinct from {@link useAuthStore}: that store deliberately keeps the access
 * token in memory only (persisting it is a security regression). This store
 * holds **non-sensitive** layout preferences, so localStorage persistence is
 * safe and desirable — a user who collapses the sidebar expects it to stay
 * collapsed on their next visit (BD / Snyk / Linear convention).
 *
 * Scope rules:
 *   - Only durable, non-sensitive UI prefs belong here. Ephemeral state (e.g.
 *     the mobile nav drawer open flag) stays as local component state so it
 *     resets on navigation and never touches storage.
 *   - `partialize` pins exactly which keys are written, so adding ephemeral
 *     fields later does not silently start persisting them.
 */
interface UIState {
  /**
   * Desktop sidebar collapsed to a 64 px icon-only rail. Persisted.
   * Has no effect below the `lg` breakpoint, where the sidebar is replaced
   * by an overlay drawer (see AppShell).
   */
  sidebarCollapsed: boolean;
  toggleSidebarCollapsed: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
}

export const useUIStore = create<UIState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      toggleSidebarCollapsed: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (sidebarCollapsed) => set({ sidebarCollapsed }),
    }),
    {
      name: "trustedoss-ui",
      // Resolve `window.localStorage` lazily *per call* instead of letting
      // zustand capture the bare `localStorage` identifier once. Two reasons:
      // (1) the bare identifier resolves to a method-less experimental global
      // under Node 22 (the vitest runtime); (2) `createJSONStorage` reads its
      // getter eagerly at module-eval, which under jsdom happens before the
      // test setup installs a working `window.localStorage` shim — so a direct
      // `() => window.localStorage` would still capture the broken reference.
      // The indirection below defers the lookup to each read/write. The app is
      // client-only, so `window` is always defined at runtime.
      storage: createJSONStorage(() => ({
        getItem: (name) => window.localStorage.getItem(name),
        setItem: (name, value) => window.localStorage.setItem(name, value),
        removeItem: (name) => window.localStorage.removeItem(name),
      })),
      partialize: (s) => ({ sidebarCollapsed: s.sidebarCollapsed }),
    },
  ),
);
