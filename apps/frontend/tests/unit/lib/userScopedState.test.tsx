// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * The query cache and the acting team belong to the signed-in user. Every
 * way that user can leave (sign-out, forced sign-out after a failed refresh,
 * another account signing in) must drop them, and nothing else may.
 *
 * The tests mount the real `AppProviders` and drive the real auth store and
 * axios interceptor, so they cover the wiring and not only the helper.
 */
import {
  MutationObserver,
  QueryClient,
  useQueryClient,
} from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppProviders } from "@/components/AppProviders";
import { accessTokenSubject, api, fetchMe } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import { bindUserScopedState } from "@/lib/userScopedState";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

function user(id: string): AuthUser {
  return {
    id,
    email: `${id}@example.com`,
    displayName: id,
    role: "developer",
    isActive: true,
    isSuperuser: false,
    teamId: `team-of-${id}`,
  };
}

/** Answers every request with `status`; the body is a minimal problem. */
function adapterAnswering(status: number): AxiosAdapter {
  return async (config: InternalAxiosRequestConfig) => {
    const response: AxiosResponse = {
      data: status >= 400 ? { title: "x", detail: "x" } : null,
      status,
      statusText: "",
      headers: {},
      config,
      request: {},
    };
    if (status >= 400) {
      const err: Error & {
        response?: AxiosResponse;
        config?: InternalAxiosRequestConfig;
        isAxiosError?: boolean;
      } = new Error(`HTTP ${status}`);
      err.response = response;
      err.config = config;
      err.isAxiosError = true;
      throw err;
    }
    return response;
  };
}

let client: QueryClient | null = null;

function Capture() {
  client = useQueryClient();
  return null;
}

function mountProviders(): QueryClient {
  render(
    <AppProviders router="none">
      <Capture />
    </AppProviders>,
  );
  if (!client) throw new Error("query client was not captured");
  return client;
}

function signIn(u: AuthUser) {
  act(() => {
    useAuthStore.setState({
      user: u,
      accessToken: `tok-${u.id}`,
      status: "authenticated",
      isAuthenticated: true,
    });
  });
}

function seed(qc: QueryClient) {
  qc.setQueryData(["projects"], [{ id: "p-1", name: "first user's project" }]);
  qc.setQueryData(["notifications", "unread"], 3);
}

const originalAdapter = api.defaults.adapter;

beforeEach(() => {
  client = null;
  useAuthStore.setState({
    user: null,
    accessToken: null,
    status: "anonymous",
    isAuthenticated: false,
  });
  useUIStore.setState({ activeTeamId: null, activeTeamOwnerId: null });
});

afterEach(() => {
  cleanup();
  api.defaults.adapter = originalAdapter;
});

describe("user-scoped state is dropped when the user changes", () => {
  it("sign-out empties the query cache and forgets the acting team", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);
    useUIStore.getState().setActiveTeamId("team-of-alice");
    api.defaults.adapter = adapterAnswering(204);

    await act(async () => {
      await useAuthStore.getState().logout();
    });

    expect(qc.getQueryData(["projects"])).toBeUndefined();
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(useUIStore.getState().activeTeamId).toBeNull();
  });

  it("sign-out still empties the cache when the logout request fails", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);
    api.defaults.adapter = adapterAnswering(500);

    await act(async () => {
      await useAuthStore.getState().logout();
    });

    expect(qc.getQueryCache().getAll()).toHaveLength(0);
  });

  it("the forced sign-out after a failed refresh empties the cache", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);
    useUIStore.getState().setActiveTeamId("team-of-alice");
    // Every call 401s: the original request, then /auth/refresh, which sends
    // the interceptor down its reset() + auth:expired path.
    api.defaults.adapter = adapterAnswering(401);

    await act(async () => {
      await expect(fetchMe()).rejects.toBeInstanceOf(ProblemError);
    });

    expect(useAuthStore.getState().user).toBeNull();
    expect(qc.getQueryData(["projects"])).toBeUndefined();
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(useUIStore.getState().activeTeamId).toBeNull();
  });

  it("another user signing in over the current one empties the cache", () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);

    act(() => {
      useAuthStore.getState().setUser(user("bob"));
    });

    expect(qc.getQueryData(["projects"])).toBeUndefined();
  });

  it("re-setting the same user (profile refresh) keeps the cache", () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);
    useUIStore.getState().setActiveTeamId("team-of-alice");

    act(() => {
      useAuthStore
        .getState()
        .setUser({ ...user("alice"), displayName: "Alice Renamed" });
    });

    expect(qc.getQueryData(["projects"])).toHaveLength(1);
    expect(useUIStore.getState().activeTeamId).toBe("team-of-alice");
  });

  it("the first sign-in of the page keeps what the sign-in screen cached", () => {
    const qc = mountProviders();
    qc.setQueryData(["auth", "providers"], ["oidc"]);

    signIn(user("alice"));

    expect(qc.getQueryData(["auth", "providers"])).toEqual(["oidc"]);
  });

  it("stops watching once unbound", () => {
    const qc = new QueryClient();
    const unbind = bindUserScopedState(qc);
    signIn(user("alice"));
    seed(qc);
    unbind();

    act(() => {
      useAuthStore.getState().reset();
    });

    expect(qc.getQueryData(["projects"])).toHaveLength(1);
  });
});

/** An unsigned JWT-shaped token whose payload carries `sub`. */
function tokenFor(sub: string): string {
  const b64url = (value: object) =>
    btoa(JSON.stringify(value))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  return `${b64url({ alg: "HS256" })}.${b64url({ sub, type: "access" })}.sig`;
}

/** First /auth/me is 401, /auth/refresh returns `token`, later /auth/me 200. */
function refreshingTo(token: string): AxiosAdapter {
  let meCalls = 0;
  return async (config: InternalAxiosRequestConfig) => {
    const url = config.url ?? "";
    if (url.endsWith("/auth/refresh")) {
      return {
        data: { access_token: token, token_type: "bearer" },
        status: 200,
        statusText: "",
        headers: {},
        config,
        request: {},
      };
    }
    meCalls += 1;
    if (meCalls === 1) return adapterAnswering(401)(config);
    return {
      data: {
        id: "alice",
        email: "alice@example.com",
        full_name: "alice",
        is_active: true,
        is_superuser: false,
      },
      status: 200,
      statusText: "",
      headers: {},
      config,
      request: {},
    };
  };
}

describe("another tab of the same browser", () => {
  it("signing out there empties this tab's cache", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);
    const otherTab = new BroadcastChannel("trusca-auth");

    try {
      otherTab.postMessage({ type: "signed-out" });
      await act(async () => {
        await vi.waitFor(() =>
          expect(useAuthStore.getState().user).toBeNull(),
        );
      });
    } finally {
      otherTab.close();
    }

    expect(qc.getQueryCache().getAll()).toHaveLength(0);
  });
});

describe("a refresh that returns another user's token", () => {
  it("signs this tab out instead of adopting the token", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);
    api.defaults.adapter = refreshingTo(tokenFor("bob"));
    let expired = 0;
    const onExpired = () => {
      expired += 1;
    };
    window.addEventListener("auth:expired", onExpired);

    try {
      await act(async () => {
        await expect(fetchMe()).rejects.toBeInstanceOf(ProblemError);
      });
    } finally {
      window.removeEventListener("auth:expired", onExpired);
    }

    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().accessToken).toBeNull();
    expect(expired).toBe(1);
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
  });

  it("treats a token whose subject cannot be read as another user's", async () => {
    mountProviders();
    signIn(user("alice"));
    api.defaults.adapter = refreshingTo("not-a-jwt");

    await act(async () => {
      await expect(fetchMe()).rejects.toBeInstanceOf(ProblemError);
    });

    expect(useAuthStore.getState().user).toBeNull();
  });

  it("keeps the session when the token is for the same user", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    seed(qc);
    const fresh = tokenFor("alice");
    api.defaults.adapter = refreshingTo(fresh);

    await act(async () => {
      await fetchMe();
    });

    expect(useAuthStore.getState().user?.id).toBe("alice");
    expect(useAuthStore.getState().accessToken).toBe(fresh);
    expect(qc.getQueryData(["projects"])).toHaveLength(1);
  });
});

describe("accessTokenSubject", () => {
  it("reads sub from a base64url payload, including - and _ characters", () => {
    // This sub encodes to base64 containing both "+" and "/".
    const sub = "u>>>??~";
    const payload = btoa(JSON.stringify({ sub }))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
    expect(payload).toMatch(/[-_]/);
    expect(accessTokenSubject(`h.${payload}.s`)).toBe(sub);
  });

  it("returns null for tokens without a readable string sub", () => {
    expect(accessTokenSubject("nodots")).toBeNull();
    expect(accessTokenSubject("h.!!!.s")).toBeNull();
    expect(accessTokenSubject(`h.${btoa("null")}.s`)).toBeNull();
    expect(accessTokenSubject(`h.${btoa('{"sub":7}')}.s`)).toBeNull();
  });
});

describe("a mutation that outlives its user", () => {
  function deferred<T>() {
    let resolve!: (v: T) => void;
    let reject!: (e: unknown) => void;
    const promise = new Promise<T>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  }

  async function flush() {
    await act(async () => {
      for (let i = 0; i < 5; i += 1) await Promise.resolve();
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it("does not write its result into the next user's cache", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    const pending = deferred<{ id: string }>();
    const handlers: string[] = [];
    const observer = new MutationObserver(qc, {
      mutationFn: () => {
        handlers.push("request sent");
        return pending.promise;
      },
      onSuccess: (data: { id: string }) => {
        handlers.push("success");
        qc.setQueryData(["finding", data.id], data);
      },
      onSettled: () => {
        handlers.push("settled");
      },
    });
    void observer.mutate(undefined).catch(() => undefined);
    // The request is on its way before the user changes.
    await flush();
    expect(handlers).toEqual(["request sent"]);

    act(() => {
      useAuthStore.getState().reset();
    });
    signIn(user("bob"));
    pending.resolve({ id: "alice-record" });
    await flush();

    expect(handlers).toEqual(["request sent"]);
    expect(qc.getQueryData(["finding", "alice-record"])).toBeUndefined();
  });

  it("does not roll back into the next user's cache when it fails", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    const pending = deferred<unknown>();
    const handlers: string[] = [];
    const observer = new MutationObserver(qc, {
      mutationFn: () => {
        handlers.push("request sent");
        return pending.promise;
      },
      onError: () => {
        handlers.push("error");
        qc.setQueryData(["projects"], [{ id: "alice-snapshot" }]);
      },
    });
    void observer.mutate(undefined).catch(() => undefined);
    // The request is on its way before the user changes.
    await flush();
    expect(handlers).toEqual(["request sent"]);

    act(() => {
      useAuthStore.getState().reset();
    });
    pending.reject(new Error("late failure"));
    await flush();

    expect(handlers).toEqual(["request sent"]);
    expect(qc.getQueryData(["projects"])).toBeUndefined();
  });

  it("settles normally while the same user stays signed in", async () => {
    const qc = mountProviders();
    signIn(user("alice"));
    const handlers: string[] = [];
    const observer = new MutationObserver(qc, {
      mutationFn: async () => ({ id: "r-1" }),
      onSuccess: (data: { id: string }) => {
        handlers.push("success");
        qc.setQueryData(["finding", data.id], data);
      },
    });
    const failing = new MutationObserver(qc, {
      mutationFn: async () => {
        throw new Error("plain failure");
      },
      onError: () => {
        handlers.push("error");
      },
      meta: { errorToast: false },
    });

    await act(async () => {
      await observer.mutate(undefined);
      await failing.mutate(undefined).catch(() => undefined);
    });

    expect(handlers).toEqual(["success", "error"]);
    expect(qc.getQueryData(["finding", "r-1"])).toEqual({ id: "r-1" });
  });
});

describe("the persisted acting team belongs to the user who chose it", () => {
  it("is dropped when a different user signs in on this browser", () => {
    useUIStore.setState({
      activeTeamId: "team-of-alice",
      activeTeamOwnerId: "alice",
    });
    mountProviders();

    signIn(user("bob"));

    expect(useUIStore.getState().activeTeamId).toBeNull();
    expect(useUIStore.getState().activeTeamOwnerId).toBe("bob");
  });

  it("is kept when the same user signs in again (a reload)", () => {
    useUIStore.setState({
      activeTeamId: "team-of-alice",
      activeTeamOwnerId: "alice",
    });
    mountProviders();

    signIn(user("alice"));

    expect(useUIStore.getState().activeTeamId).toBe("team-of-alice");
  });

  it("is dropped when it was saved without an owner", () => {
    useUIStore.setState({
      activeTeamId: "team-unknown",
      activeTeamOwnerId: null,
    });
    mountProviders();

    signIn(user("bob"));

    expect(useUIStore.getState().activeTeamId).toBeNull();
    expect(useUIStore.getState().activeTeamOwnerId).toBe("bob");
  });

  it("is checked at mount when a user is already signed in", () => {
    useUIStore.setState({
      activeTeamId: "team-of-alice",
      activeTeamOwnerId: "alice",
    });
    signIn(user("bob"));

    mountProviders();

    expect(useUIStore.getState().activeTeamId).toBeNull();
  });

  it("records its owner so later choices survive a reload", () => {
    mountProviders();
    signIn(user("alice"));
    useUIStore.getState().setActiveTeamId("team-of-alice");

    const persisted = JSON.parse(
      window.localStorage.getItem("trustedoss-ui") as string,
    ) as { state: { activeTeamId: string; activeTeamOwnerId: string } };

    expect(persisted.state.activeTeamId).toBe("team-of-alice");
    expect(persisted.state.activeTeamOwnerId).toBe("alice");
  });
});
