import { beforeEach, describe, expect, it, vi } from "vitest";

import { useAuthStore, type AuthUser } from "@/stores/authStore";

vi.mock("@/lib/api", () => ({
  fetchMe: vi.fn(),
  postLogout: vi.fn(),
  postLogin: vi.fn(),
  postRegister: vi.fn(),
}));

import { fetchMe, postLogout } from "@/lib/api";

const mockedFetchMe = vi.mocked(fetchMe);
const mockedPostLogout = vi.mocked(postLogout);

const sampleUser: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: null,
};

describe("authStore", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "idle",
      isAuthenticated: false,
    });
    mockedFetchMe.mockReset();
    mockedPostLogout.mockReset();
  });

  it("starts in idle status with no user/token/auth", () => {
    const state = useAuthStore.getState();
    expect(state.status).toBe("idle");
    expect(state.isAuthenticated).toBe(false);
    expect(state.user).toBeNull();
    expect(state.accessToken).toBeNull();
  });

  it("setStatus('authenticated') flips isAuthenticated", () => {
    useAuthStore.getState().setStatus("authenticated");
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    useAuthStore.getState().setStatus("anonymous");
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });

  it("setUser stores the user but does not flip authentication on its own", () => {
    useAuthStore.getState().setUser(sampleUser);
    const state = useAuthStore.getState();
    expect(state.user).toEqual(sampleUser);
    // status only flips through setStatus / bootstrap.
    expect(state.isAuthenticated).toBe(false);
  });

  it("setAccessToken stores the token without touching status", () => {
    useAuthStore.getState().setAccessToken("token-abc");
    expect(useAuthStore.getState().accessToken).toBe("token-abc");
    expect(useAuthStore.getState().status).toBe("idle");
  });

  it("reset clears user, token, and lands on anonymous", () => {
    useAuthStore.getState().setUser(sampleUser);
    useAuthStore.getState().setAccessToken("token-abc");
    useAuthStore.getState().setStatus("authenticated");
    useAuthStore.getState().reset();

    const state = useAuthStore.getState();
    expect(state.user).toBeNull();
    expect(state.accessToken).toBeNull();
    expect(state.status).toBe("anonymous");
    expect(state.isAuthenticated).toBe(false);
  });

  it("bootstrap → /auth/me success → authenticated", async () => {
    mockedFetchMe.mockResolvedValueOnce(sampleUser);

    await useAuthStore.getState().bootstrap();

    const state = useAuthStore.getState();
    expect(mockedFetchMe).toHaveBeenCalledTimes(1);
    expect(state.user).toEqual(sampleUser);
    expect(state.status).toBe("authenticated");
    expect(state.isAuthenticated).toBe(true);
  });

  it("bootstrap → /auth/me failure → anonymous (and no stale token)", async () => {
    useAuthStore.getState().setAccessToken("stale-token");
    mockedFetchMe.mockRejectedValueOnce(new Error("401"));

    await useAuthStore.getState().bootstrap();

    const state = useAuthStore.getState();
    expect(state.status).toBe("anonymous");
    expect(state.isAuthenticated).toBe(false);
    expect(state.user).toBeNull();
    expect(state.accessToken).toBeNull();
  });

  it("bootstrap is a no-op when already authenticated", async () => {
    useAuthStore.setState({
      user: sampleUser,
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
    await useAuthStore.getState().bootstrap();
    expect(mockedFetchMe).not.toHaveBeenCalled();
  });

  it("logout calls /auth/logout and resets state even if the call throws", async () => {
    useAuthStore.setState({
      user: sampleUser,
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
    mockedPostLogout.mockRejectedValueOnce(new Error("network"));

    await useAuthStore.getState().logout();

    expect(mockedPostLogout).toHaveBeenCalledTimes(1);
    const state = useAuthStore.getState();
    expect(state.status).toBe("anonymous");
    expect(state.user).toBeNull();
    expect(state.accessToken).toBeNull();
  });

  it("logout success path also resets state", async () => {
    useAuthStore.setState({
      user: sampleUser,
      accessToken: "tok",
      status: "authenticated",
      isAuthenticated: true,
    });
    mockedPostLogout.mockResolvedValueOnce(undefined);

    await useAuthStore.getState().logout();
    expect(useAuthStore.getState().status).toBe("anonymous");
  });
});
