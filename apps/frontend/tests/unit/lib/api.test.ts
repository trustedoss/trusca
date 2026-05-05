/**
 * api.ts — interceptor unit tests.
 *
 * We swap the axios `adapter` so the request never leaves the JS runtime;
 * this lets us drive arbitrary status sequences (401 → refresh → 200) and
 * assert the interceptor's behaviour without a network or axios-mock-adapter.
 *
 * Coverage targets:
 *   - request interceptor attaches Bearer header from the store.
 *   - 401 → POST /auth/refresh → original request replayed with new token.
 *   - concurrent 401s coalesce into ONE refresh (singleflight).
 *   - /auth/refresh failing dispatches `auth:expired` and resets the store.
 *   - non-401 errors surface as ProblemError carrying the parsed problem body.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, fetchMe, postLogin } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

interface CannedResponse {
  status: number;
  data?: unknown;
  headers?: Record<string, string>;
  statusText?: string;
}

interface RequestRecord {
  method: string;
  url: string;
  authorization: string | null;
}

/**
 * Build a stub adapter that responds based on (method, url) routing. Unknown
 * routes throw to surface accidental new requests in tests.
 */
function buildAdapter(routes: Array<{
  method: string;
  url: RegExp | string;
  respond: (call: number) => CannedResponse;
}>) {
  const calls: RequestRecord[] = [];
  const counters = new Map<number, number>();
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    const method = (config.method || "get").toLowerCase();
    const url = config.url || "";
    const auth =
      (config.headers as Record<string, unknown> | undefined)?.[
        "Authorization"
      ];
    calls.push({
      method,
      url,
      authorization: typeof auth === "string" ? auth : null,
    });
    for (let i = 0; i < routes.length; i++) {
      const route = routes[i];
      const urlMatches =
        typeof route.url === "string"
          ? url === route.url || url.endsWith(route.url)
          : route.url.test(url);
      if (route.method.toLowerCase() === method && urlMatches) {
        const n = counters.get(i) ?? 0;
        counters.set(i, n + 1);
        const canned = route.respond(n);
        const response: AxiosResponse = {
          data: canned.data ?? null,
          status: canned.status,
          statusText: canned.statusText ?? "",
          headers: canned.headers ?? {},
          config,
          request: {},
        };
        if (canned.status >= 400) {
          // Match axios' real error shape so the response interceptor sees
          // `error.response`.
          const err: Error & {
            response?: AxiosResponse;
            config?: InternalAxiosRequestConfig;
            isAxiosError?: boolean;
          } = new Error(`HTTP ${canned.status}`);
          err.response = response;
          err.config = config;
          err.isAxiosError = true;
          throw err;
        }
        return response;
      }
    }
    throw new Error(`unstubbed ${method.toUpperCase()} ${url}`);
  };
  return { adapter, calls };
}

const originalAdapter = api.defaults.adapter;

beforeEach(() => {
  useAuthStore.setState({
    user: null,
    accessToken: null,
    status: "anonymous",
    isAuthenticated: false,
  });
});

afterEach(() => {
  api.defaults.adapter = originalAdapter;
});

describe("api.ts request interceptor", () => {
  it("attaches the bearer token from the store", async () => {
    useAuthStore.getState().setAccessToken("tok-attach");
    const { adapter, calls } = buildAdapter([
      {
        method: "get",
        url: "/auth/me",
        respond: () => ({
          status: 200,
          data: {
            id: "u-1",
            email: "a@b.c",
            full_name: "A",
            is_active: true,
            is_superuser: false,
            created_at: "2026-05-05T00:00:00Z",
          },
        }),
      },
    ]);
    api.defaults.adapter = adapter;

    await fetchMe();
    expect(calls[0]?.authorization).toBe("Bearer tok-attach");
  });

  it("does not set Authorization when there is no token", async () => {
    const { adapter, calls } = buildAdapter([
      {
        method: "post",
        url: "/auth/login",
        respond: () => ({
          status: 200,
          data: { access_token: "x", token_type: "bearer", expires_in: 1800 },
        }),
      },
    ]);
    api.defaults.adapter = adapter;

    await postLogin({ email: "a@b.c", password: "twelvechars1!" });
    expect(calls[0]?.authorization).toBeNull();
  });
});

describe("api.ts response interceptor — 401 → refresh → retry", () => {
  it("refreshes once and replays the original request with the new token", async () => {
    useAuthStore.getState().setAccessToken("tok-old");
    const { adapter, calls } = buildAdapter([
      {
        method: "get",
        url: "/auth/me",
        respond: (n) =>
          n === 0
            ? { status: 401, data: { title: "expired", detail: "expired" } }
            : {
                status: 200,
                data: {
                  id: "u-1",
                  email: "a@b.c",
                  full_name: "A",
                  is_active: true,
                  is_superuser: false,
                  created_at: "2026-05-05T00:00:00Z",
                },
              },
      },
      {
        method: "post",
        url: "/auth/refresh",
        respond: () => ({
          status: 200,
          data: { access_token: "tok-new", token_type: "bearer", expires_in: 1800 },
        }),
      },
    ]);
    api.defaults.adapter = adapter;

    const me = await fetchMe();
    expect(me.email).toBe("a@b.c");

    // Sequence: GET /auth/me (Bearer tok-old, 401) → POST /auth/refresh (200)
    // → GET /auth/me retried (Bearer tok-new, 200).
    expect(calls).toHaveLength(3);
    expect(calls[0]?.url).toContain("/auth/me");
    expect(calls[0]?.authorization).toBe("Bearer tok-old");
    expect(calls[1]?.url).toContain("/auth/refresh");
    expect(calls[2]?.url).toContain("/auth/me");
    expect(calls[2]?.authorization).toBe("Bearer tok-new");
    expect(useAuthStore.getState().accessToken).toBe("tok-new");
  });

  it("coalesces concurrent 401s into a single /auth/refresh (singleflight)", async () => {
    useAuthStore.getState().setAccessToken("tok-old");
    let refreshCount = 0;
    const { adapter, calls } = buildAdapter([
      {
        method: "get",
        url: /\/auth\/me/,
        respond: (n) =>
          n < 2
            ? { status: 401, data: { title: "expired", detail: "expired" } }
            : {
                status: 200,
                data: {
                  id: "u-1",
                  email: "a@b.c",
                  full_name: "A",
                  is_active: true,
                  is_superuser: false,
                  created_at: "2026-05-05T00:00:00Z",
                },
              },
      },
      {
        method: "post",
        url: "/auth/refresh",
        respond: () => {
          refreshCount += 1;
          return {
            status: 200,
            data: {
              access_token: "tok-new",
              token_type: "bearer",
              expires_in: 1800,
            },
          };
        },
      },
    ]);
    api.defaults.adapter = adapter;

    const [a, b] = await Promise.all([fetchMe(), fetchMe()]);
    expect(a.email).toBe("a@b.c");
    expect(b.email).toBe("a@b.c");
    expect(refreshCount).toBe(1);
    // Both retries land with tok-new.
    const meCalls = calls.filter((c) => c.url.includes("/auth/me"));
    expect(meCalls).toHaveLength(4);
    expect(meCalls.slice(2).every((c) => c.authorization === "Bearer tok-new"))
      .toBe(true);
  });

  it("L-2: concurrent 401s with refresh failure → exactly ONE auth:expired event", async () => {
    // Two parallel authenticated requests both 401 in the same tick. They
    // share the same singleflight refresh promise (which fails). The fix is
    // that reset() + dispatchEvent('auth:expired') happen inside refreshOnce()
    // — so each shared awaiter no longer fires its own copy.
    useAuthStore.setState({
      user: {
        id: "u-1",
        email: "a@b.c",
        displayName: "A",
        role: "developer",
        isActive: true,
        isSuperuser: false,
        teamId: null,
      },
      accessToken: "tok-old",
      status: "authenticated",
      isAuthenticated: true,
    });
    let refreshCount = 0;
    const { adapter } = buildAdapter([
      {
        method: "get",
        url: /\/auth\/me/,
        respond: () => ({
          status: 401,
          data: { title: "expired", detail: "expired" },
        }),
      },
      {
        method: "post",
        url: "/auth/refresh",
        respond: () => {
          refreshCount += 1;
          return {
            status: 401,
            data: { title: "no_session", detail: "no refresh cookie" },
          };
        },
      },
    ]);
    api.defaults.adapter = adapter;

    const expiredHandler = vi.fn();
    window.addEventListener("auth:expired", expiredHandler);
    try {
      const results = await Promise.allSettled([fetchMe(), fetchMe()]);
      // Both reject with ProblemError.
      expect(results.every((r) => r.status === "rejected")).toBe(true);
      for (const r of results) {
        if (r.status === "rejected") {
          expect(r.reason).toBeInstanceOf(ProblemError);
        }
      }
    } finally {
      window.removeEventListener("auth:expired", expiredHandler);
    }
    // Singleflight: only ONE /auth/refresh attempt.
    expect(refreshCount).toBe(1);
    // L-2 invariant: one event, regardless of how many awaiters.
    expect(expiredHandler).toHaveBeenCalledTimes(1);
    // Store reset is also idempotent — once.
    const state = useAuthStore.getState();
    expect(state.status).toBe("anonymous");
    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
  });

  it("dispatches auth:expired and resets state when /auth/refresh itself 401s", async () => {
    useAuthStore.setState({
      user: {
        id: "u-1",
        email: "a@b.c",
        displayName: "A",
        role: "developer",
        isActive: true,
        isSuperuser: false,
        teamId: null,
      },
      accessToken: "tok-old",
      status: "authenticated",
      isAuthenticated: true,
    });
    const { adapter } = buildAdapter([
      {
        method: "get",
        url: "/auth/me",
        respond: () => ({
          status: 401,
          data: { title: "expired", detail: "expired" },
        }),
      },
      {
        method: "post",
        url: "/auth/refresh",
        respond: () => ({
          status: 401,
          data: { title: "no_session", detail: "no refresh cookie" },
        }),
      },
    ]);
    api.defaults.adapter = adapter;

    const expiredHandler = vi.fn();
    window.addEventListener("auth:expired", expiredHandler);
    try {
      await expect(fetchMe()).rejects.toBeInstanceOf(ProblemError);
    } finally {
      window.removeEventListener("auth:expired", expiredHandler);
    }
    expect(expiredHandler).toHaveBeenCalledTimes(1);
    const state = useAuthStore.getState();
    expect(state.status).toBe("anonymous");
    expect(state.accessToken).toBeNull();
    expect(state.user).toBeNull();
  });
});

describe("api.ts response interceptor — error mapping", () => {
  it("converts a 422 RFC 7807 body into ProblemError preserving detail", async () => {
    const { adapter } = buildAdapter([
      {
        method: "post",
        url: "/auth/login",
        respond: () => ({
          status: 422,
          data: {
            type: "about:blank",
            title: "validation_error",
            status: 422,
            detail: "password must be at least 12 characters",
            instance: "/auth/login",
          },
        }),
      },
    ]);
    api.defaults.adapter = adapter;

    try {
      await postLogin({ email: "a@b.c", password: "twelvechars1!" });
      expect.fail("expected ProblemError");
    } catch (err) {
      expect(err).toBeInstanceOf(ProblemError);
      const pe = err as ProblemError;
      expect(pe.status).toBe(422);
      expect(pe.detail).toMatch(/at least 12 characters/i);
      expect(pe.problem?.type).toBe("about:blank");
    }
  });

  it("converts transport failures into ProblemError(status=0)", async () => {
    api.defaults.adapter = async () => {
      const err: Error & { isAxiosError?: boolean } = new Error("ECONNREFUSED");
      err.isAxiosError = true;
      throw err;
    };

    try {
      await postLogin({ email: "a@b.c", password: "twelvechars1!" });
      expect.fail("expected ProblemError");
    } catch (err) {
      expect(err).toBeInstanceOf(ProblemError);
      const pe = err as ProblemError;
      expect(pe.status).toBe(0);
      expect(pe.detail).toMatch(/ECONNREFUSED/);
    }
  });
});
