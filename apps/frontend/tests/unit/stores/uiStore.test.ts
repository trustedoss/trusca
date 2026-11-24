import { beforeEach, describe, expect, it } from "vitest";

import { useUIStore } from "@/stores/uiStore";

describe("uiStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({ sidebarCollapsedChoice: null });
  });

  it("starts with no explicit sidebar choice", () => {
    expect(useUIStore.getState().sidebarCollapsedChoice).toBeNull();
  });

  it("toggleSidebarCollapsed flips the effective state both ways", () => {
    // The test matchMedia stub is wide (>= 1280), so the default is expanded.
    useUIStore.getState().toggleSidebarCollapsed();
    expect(useUIStore.getState().sidebarCollapsedChoice).toBe(true);
    useUIStore.getState().toggleSidebarCollapsed();
    expect(useUIStore.getState().sidebarCollapsedChoice).toBe(false);
  });

  it("setSidebarCollapsed sets an explicit value", () => {
    useUIStore.getState().setSidebarCollapsed(true);
    expect(useUIStore.getState().sidebarCollapsedChoice).toBe(true);
    useUIStore.getState().setSidebarCollapsed(false);
    expect(useUIStore.getState().sidebarCollapsedChoice).toBe(false);
  });

  it("persists the choice to localStorage under trustedoss-ui", () => {
    useUIStore.getState().setSidebarCollapsed(true);
    const raw = window.localStorage.getItem("trustedoss-ui");
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string).state.sidebarCollapsedChoice).toBe(true);
  });
});
