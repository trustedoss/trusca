import { beforeEach, describe, expect, it } from "vitest";

import { useUIStore } from "@/stores/uiStore";

describe("uiStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({ sidebarCollapsed: false });
  });

  it("defaults to an expanded sidebar", () => {
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it("toggleSidebarCollapsed flips the flag both ways", () => {
    useUIStore.getState().toggleSidebarCollapsed();
    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
    useUIStore.getState().toggleSidebarCollapsed();
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it("setSidebarCollapsed sets an explicit value", () => {
    useUIStore.getState().setSidebarCollapsed(true);
    expect(useUIStore.getState().sidebarCollapsed).toBe(true);
    useUIStore.getState().setSidebarCollapsed(false);
    expect(useUIStore.getState().sidebarCollapsed).toBe(false);
  });

  it("persists the collapsed flag to localStorage under trustedoss-ui", () => {
    useUIStore.getState().setSidebarCollapsed(true);
    const raw = window.localStorage.getItem("trustedoss-ui");
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw as string).state.sidebarCollapsed).toBe(true);
  });
});
