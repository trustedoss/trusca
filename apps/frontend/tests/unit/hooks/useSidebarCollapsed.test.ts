import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useSidebarCollapsed } from "@/hooks/useSidebarCollapsed";
import { useUIStore } from "@/stores/uiStore";

const originalMatchMedia = window.matchMedia;

/** Install a matchMedia whose min-width 1280 query follows `width`. */
function installViewport(initialWidth: number) {
  let width = initialWidth;
  const listeners = new Set<(e: MediaQueryListEvent) => void>();
  window.matchMedia = ((query: string) => ({
    get matches() {
      return query.includes("min-width: 1280px") ? width >= 1280 : false;
    },
    media: query,
    addEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) =>
      listeners.add(fn),
    removeEventListener: (_: string, fn: (e: MediaQueryListEvent) => void) =>
      listeners.delete(fn),
  })) as unknown as typeof window.matchMedia;
  return (next: number) => {
    width = next;
    listeners.forEach((fn) =>
      fn({ matches: next >= 1280 } as MediaQueryListEvent),
    );
  };
}

describe("useSidebarCollapsed", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({ sidebarCollapsedChoice: null });
  });
  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  it("no choice at 1024 -> collapsed", () => {
    installViewport(1024);
    expect(renderHook(() => useSidebarCollapsed()).result.current).toBe(true);
  });

  it("no choice at 1280 -> expanded", () => {
    installViewport(1280);
    expect(renderHook(() => useSidebarCollapsed()).result.current).toBe(false);
  });

  it("explicit expand at 1024 stays expanded", () => {
    installViewport(1024);
    useUIStore.setState({ sidebarCollapsedChoice: false });
    expect(renderHook(() => useSidebarCollapsed()).result.current).toBe(false);
  });

  it("explicit collapse at 1440 stays collapsed", () => {
    installViewport(1440);
    useUIStore.setState({ sidebarCollapsedChoice: true });
    expect(renderHook(() => useSidebarCollapsed()).result.current).toBe(true);
  });

  it("follows a resize across 1280 when there is no choice", () => {
    const resize = installViewport(1440);
    const { result } = renderHook(() => useSidebarCollapsed());
    expect(result.current).toBe(false);
    act(() => resize(1100));
    expect(result.current).toBe(true);
    act(() => resize(1300));
    expect(result.current).toBe(false);
  });

  it("ignores a resize once the user has chosen", () => {
    const resize = installViewport(1440);
    useUIStore.setState({ sidebarCollapsedChoice: false });
    const { result } = renderHook(() => useSidebarCollapsed());
    act(() => resize(1100));
    expect(result.current).toBe(false);
  });

  it("toggle at 1024 with no choice expands explicitly", () => {
    installViewport(1024);
    act(() => useUIStore.getState().toggleSidebarCollapsed());
    expect(useUIStore.getState().sidebarCollapsedChoice).toBe(false);
  });
});
