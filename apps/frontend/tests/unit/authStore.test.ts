import { beforeEach, describe, expect, it } from "vitest";

import { useAuthStore, type AuthUser } from "@/stores/authStore";

const sampleUser: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  teamId: "team-1",
};

describe("authStore (Phase 0 placeholder)", () => {
  beforeEach(() => {
    useAuthStore.getState().reset();
  });

  it("starts unauthenticated with no user or token", () => {
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.user).toBeNull();
    expect(state.accessToken).toBeNull();
  });

  it("setUser flips isAuthenticated and stores the user", () => {
    useAuthStore.getState().setUser(sampleUser);
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(true);
    expect(state.user).toEqual(sampleUser);
  });

  it("setAccessToken stores the token without touching isAuthenticated", () => {
    useAuthStore.getState().setAccessToken("token-abc");
    expect(useAuthStore.getState().accessToken).toBe("token-abc");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("reset clears user, token, and authentication flag", () => {
    useAuthStore.getState().setUser(sampleUser);
    useAuthStore.getState().setAccessToken("token-abc");
    useAuthStore.getState().reset();

    const state = useAuthStore.getState();
    expect(state.user).toBeNull();
    expect(state.accessToken).toBeNull();
    expect(state.isAuthenticated).toBe(false);
  });
});
