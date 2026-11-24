// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
import { useEffect, useState } from "react";

import {
  SIDEBAR_WIDE_QUERY,
  isWideViewport,
  useUIStore,
} from "@/stores/uiStore";

/**
 * Effective desktop sidebar state. An explicit user choice (either value)
 * always wins; with no choice the sidebar is the 64 px rail below 1280 px and
 * expanded from 1280 px up. Tracks resizes through a matchMedia listener.
 */
export function useSidebarCollapsed(): boolean {
  const choice = useUIStore((s) => s.sidebarCollapsedChoice);
  const [wide, setWide] = useState(isWideViewport);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(SIDEBAR_WIDE_QUERY);
    const onChange = (e: MediaQueryListEvent) => setWide(e.matches);
    setWide(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return choice ?? !wide;
}
