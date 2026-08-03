/**
 * adminTeamsApi — unit tests. Mirrors the adapter-stub pattern from
 * `adminUsersApi.test.ts`.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  addTeamMember,
  createTeam,
  deleteTeam,
  getAdminTeam,
  listAdminTeams,
  removeTeamMember,
  updateTeam,
} from "@/features/admin/api/adminTeamsApi";
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

describe("adminTeamsApi", () => {
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

  it("listAdminTeams serializes search and pagination", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { items: [], total: 0, page: 1, page_size: 50 } },
    ]);
    restore = r;
    await listAdminTeams({ page: 1, page_size: 25, search: "core" });
    expect(calls[0].url).toBe("/v1/admin/teams");
    expect(calls[0].params).toMatchObject({
      page: 1,
      page_size: 25,
      search: "core",
    });
  });

  it("createTeam posts payload with description default null", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 201, data: { id: "t1", name: "Core", slug: "core" } },
    ]);
    restore = r;
    const result = await createTeam({ name: "Core", slug: "core" });
    expect(result.id).toBe("t1");
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/admin/teams");
    expect(calls[0].data).toEqual({
      name: "Core",
      slug: "core",
      description: null,
    });
  });

  it("updateTeam drops nullish fields from PATCH body", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "t1" } },
    ]);
    restore = r;
    await updateTeam("t1", { name: "Renamed" });
    expect(calls[0].method).toBe("patch");
    expect(calls[0].url).toBe("/v1/admin/teams/t1");
    expect(calls[0].data).toEqual({ name: "Renamed" });
  });

  it("updateTeam preserves explicit null description", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "t1" } },
    ]);
    restore = r;
    await updateTeam("t1", { description: null });
    expect(calls[0].data).toEqual({ description: null });
  });

  it("getAdminTeam hits the per-id route", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "t1" } },
    ]);
    restore = r;
    await getAdminTeam("t1");
    expect(calls[0].url).toBe("/v1/admin/teams/t1");
  });

  it("deleteTeam DELETEs", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 204, data: null },
    ]);
    restore = r;
    await deleteTeam("t1");
    expect(calls[0].method).toBe("delete");
    expect(calls[0].url).toBe("/v1/admin/teams/t1");
  });

  it("addTeamMember posts user_id + role", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "t1" } },
    ]);
    restore = r;
    await addTeamMember("t1", { user_id: "u1", role: "developer" });
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/admin/teams/t1/members");
    expect(calls[0].data).toEqual({ user_id: "u1", role: "developer" });
  });

  it("removeTeamMember DELETEs the member subroute", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "t1" } },
    ]);
    restore = r;
    await removeTeamMember("t1", "u1");
    expect(calls[0].method).toBe("delete");
    expect(calls[0].url).toBe("/v1/admin/teams/t1/members/u1");
  });
});
