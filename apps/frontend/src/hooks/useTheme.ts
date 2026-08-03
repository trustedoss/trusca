// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Theme state (W18).
 *
 * A module-level value rather than React state, with subscribers, because two
 * places need the same answer: whatever renders the toggle, and anything that
 * has to hand a colour to a library that cannot read CSS variables (the
 * dependency graph, the charts). React state in one component would leave the
 * second caller reading the DOM to find out.
 */
import { useCallback, useEffect, useState } from "react";

import {
  applyTheme,
  readStoredPreference,
  resolvePreference,
  storePreference,
  type ResolvedTheme,
  type ThemePreference,
} from "@/lib/theme";

let preference: ThemePreference = "system";
let resolved: ResolvedTheme = "light";
const subscribers = new Set<() => void>();

function notify(): void {
  subscribers.forEach((fn) => fn());
}

/**
 * Read the stored preference and apply it.
 *
 * Called once from `main.tsx`. The inline script in `index.html` has already
 * put the class on the document — this is what teaches the React side what
 * the script decided, and what starts listening for OS changes.
 */
export function initTheme(): void {
  preference = readStoredPreference();
  resolved = resolvePreference(preference);
  applyTheme(resolved);

  if (typeof window.matchMedia === "function") {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    query.addEventListener("change", () => {
      // Only follow the OS while the user has not overridden it. Someone who
      // explicitly chose light does not want their laptop's sunset schedule
      // changing the app underneath them.
      if (preference !== "system") return;
      resolved = resolvePreference("system");
      applyTheme(resolved);
      notify();
    });
  }
}

export function setThemePreference(next: ThemePreference): void {
  preference = next;
  storePreference(next);
  resolved = resolvePreference(next);
  applyTheme(resolved);
  notify();
}

export function getResolvedTheme(): ResolvedTheme {
  return resolved;
}

export interface UseThemeResult {
  /** What the user asked for, including `system`. */
  preference: ThemePreference;
  /** What is painted right now. */
  theme: ResolvedTheme;
  setPreference: (next: ThemePreference) => void;
}

export function useTheme(): UseThemeResult {
  const [, forceRender] = useState(0);

  useEffect(() => {
    const bump = () => forceRender((n) => n + 1);
    subscribers.add(bump);
    return () => {
      subscribers.delete(bump);
    };
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setThemePreference(next);
  }, []);

  return { preference, theme: resolved, setPreference };
}
