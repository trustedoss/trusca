/**
 * apiKeysApi — unit tests (chore C).
 *
 * Stubs the axios adapter on the shared `api` instance so the wrapper
 * functions hit the request interceptor (Bearer header) and the response
 * interceptor (ProblemError mapping) for free. Mirrors the pattern in
 * tests/unit/lib/projectsApi.test.ts.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/lib/api";
import { createApiKey, listApiKeys, revokeApiKey } from "@/lib/apiKeysApi";
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

describe("apiKeysApi", () => {
  let restore: () => void = () => {};
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: "tok-keys",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    restore();
    useAuthStore.getState().reset();
  });

  it("listApiKeys forwards every filter param", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 200,
        data: { items: [], total: 0, page: 1, page_size: 20 },
      },
    ]);
    restore = r;
    await listApiKeys({
      scope: "team",
      team_id: "t1",
      project_id: "p1",
      include_revoked: true,
      page: 2,
      page_size: 50,
    });
    expect(calls[0].method).toBe("get");
    expect(calls[0].url).toBe("/v1/api-keys");
    expect(calls[0].params).toMatchObject({
      scope: "team",
      team_id: "t1",
      project_id: "p1",
      include_revoked: true,
      page: 2,
      page_size: 50,
    });
  });

  it("listApiKeys is callable with no params (defaults)", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 200,
        data: { items: [], total: 0, page: 1, page_size: 50 },
      },
    ]);
    restore = r;
    await listApiKeys();
    expect(calls[0].url).toBe("/v1/api-keys");
    expect(calls[0].method).toBe("get");
  });

  it("createApiKey POSTs the payload with explicit nulls for missing scope ids", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 201,
        data: {
          id: "k-1",
          key_prefix: "tos_aaaa",
          name: "ci",
          scope: "org",
          team_id: null,
          project_id: null,
          created_by_user_id: "u-1",
          created_at: "2026-05-09T00:00:00Z",
          raw_key: "tos_aaaa_secret",
        },
      },
    ]);
    restore = r;

    const out = await createApiKey({ name: "ci", scope: "org" });
    expect(out.raw_key).toBe("tos_aaaa_secret");
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/api-keys");
    expect(calls[0].data).toMatchObject({
      name: "ci",
      scope: "org",
      team_id: null,
      project_id: null,
    });
  });

  it("createApiKey passes through team_id when scope='team'", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 201,
        data: {
          id: "k-2",
          key_prefix: "tos_bbbb",
          name: "team-ci",
          scope: "team",
          team_id: "t-1",
          project_id: null,
          created_by_user_id: "u-1",
          created_at: "2026-05-09T00:00:00Z",
          raw_key: "tos_bbbb_secret",
        },
      },
    ]);
    restore = r;
    await createApiKey({ name: "team-ci", scope: "team", team_id: "t-1" });
    expect(calls[0].data).toMatchObject({
      name: "team-ci",
      scope: "team",
      team_id: "t-1",
      project_id: null,
    });
  });

  it("revokeApiKey DELETEs the per-id route", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 204, data: null },
    ]);
    restore = r;
    await revokeApiKey("k-99");
    expect(calls[0].method).toBe("delete");
    expect(calls[0].url).toBe("/v1/api-keys/k-99");
  });
});
