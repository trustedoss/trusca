/**
 * adminUsersApi — unit tests.
 *
 * Same adapter-stub trick used by `lib/projectsApi.test.ts`: we install a
 * canned adapter on the shared axios instance so calls run through the real
 * interceptors (Bearer header, ProblemError mapping) without touching a
 * network. We assert URL, method, body, and query params for every wrapper
 * function.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  activateUser,
  deactivateUser,
  getAdminUser,
  listAdminUsers,
  requestPasswordReset,
  updateUserRole,
} from "@/features/admin/api/adminUsersApi";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

interface Recorded {
  method: string;
  url: string;
  data: unknown;
  params: Record<string, unknown>;
}

function installAdapter(
  responses: Array<{ status: number; data: unknown }>,
): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = [];
  const original = api.defaults.adapter;
  let i = 0;
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    const canned = responses[i] ?? { status: 200, data: null };
    i += 1;
    calls.push({
      method: (config.method ?? "get").toLowerCase(),
      url: config.url ?? "",
      data: config.data ? JSON.parse(config.data as string) : undefined,
      params: (config.params as Record<string, unknown>) ?? {},
    });
    const response: AxiosResponse = {
      data: canned.data,
      status: canned.status,
      statusText: "",
      headers: {},
      config,
      request: {},
    };
    if (canned.status >= 400) {
      const err = new Error(`status ${canned.status}`);
      (err as { response?: AxiosResponse }).response = response;
      (err as { config?: InternalAxiosRequestConfig }).config = config;
      throw err;
    }
    return response;
  };
  api.defaults.adapter = adapter;
  return {
    calls,
    restore: () => {
      api.defaults.adapter = original;
    },
  };
}

describe("adminUsersApi", () => {
  let restore: () => void = () => {};
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: "tok-admin",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    restore();
    useAuthStore.getState().reset();
  });

  it("listAdminUsers serializes filter params", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 200,
        data: { items: [], total: 0, page: 1, page_size: 50 },
      },
    ]);
    restore = r;
    await listAdminUsers({
      page: 2,
      page_size: 25,
      role: "team_admin",
      active: false,
      search: "alice",
    });
    expect(calls[0].method).toBe("get");
    expect(calls[0].url).toBe("/v1/admin/users");
    expect(calls[0].params).toMatchObject({
      page: 2,
      page_size: 25,
      role: "team_admin",
      active: false,
      search: "alice",
    });
  });

  it("listAdminUsers omits null filters from the wire", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 200,
        data: { items: [], total: 0, page: 1, page_size: 50 },
      },
    ]);
    restore = r;
    await listAdminUsers({ role: null, active: null, search: null });
    // axios drops `undefined` from the qs serialization but keeps `null`. We
    // explicitly map null → undefined upstream.
    expect(calls[0].params.role).toBeUndefined();
    expect(calls[0].params.active).toBeUndefined();
    expect(calls[0].params.search).toBeUndefined();
  });

  it("getAdminUser hits the per-id route", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "u1" } },
    ]);
    restore = r;
    await getAdminUser("u1");
    expect(calls[0].url).toBe("/v1/admin/users/u1");
    expect(calls[0].method).toBe("get");
  });

  it("updateUserRole patches the role with team_id", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "u1" } },
    ]);
    restore = r;
    await updateUserRole("u1", { role: "team_admin", team_id: "t1" });
    expect(calls[0].method).toBe("patch");
    expect(calls[0].url).toBe("/v1/admin/users/u1/role");
    expect(calls[0].data).toEqual({ role: "team_admin", team_id: "t1" });
  });

  it("updateUserRole omits team_id for super_admin", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "u1" } },
    ]);
    restore = r;
    await updateUserRole("u1", { role: "super_admin" });
    expect(calls[0].data).toEqual({ role: "super_admin", team_id: null });
  });

  it("deactivateUser/activateUser hit the right endpoints", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "u1" } },
      { status: 200, data: { id: "u1" } },
    ]);
    restore = r;
    await deactivateUser("u1");
    await activateUser("u1");
    expect(calls[0].method).toBe("patch");
    expect(calls[0].url).toBe("/v1/admin/users/u1/deactivate");
    expect(calls[1].method).toBe("patch");
    expect(calls[1].url).toBe("/v1/admin/users/u1/activate");
  });

  it("requestPasswordReset POSTs and tolerates 204", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 204, data: null },
    ]);
    restore = r;
    await requestPasswordReset("u1");
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/admin/users/u1/password-reset");
  });
});
