/**
 * projectsApi — unit tests (PR #9 task 2.11).
 *
 * We stub the axios adapter on the shared `api` instance so the wrapper
 * functions hit the request interceptor (Bearer header) and the response
 * interceptor (ProblemError mapping) for free.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/lib/api";
import {
  archiveProject,
  createProject,
  getProject,
  getScan,
  listProjects,
  listScans,
  triggerScan,
  updateProject,
} from "@/lib/projectsApi";
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

describe("projectsApi", () => {
  let restore: () => void = () => {};
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: "tok-projects",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    restore();
    useAuthStore.getState().reset();
  });

  it("listProjects sends the page/size/q params", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { items: [], total: 0, page: 1, size: 20 } },
    ]);
    restore = r;
    await listProjects({ page: 2, size: 50, q: "alpha" });
    expect(calls[0].method).toBe("get");
    expect(calls[0].url).toBe("/v1/projects");
    expect(calls[0].params).toMatchObject({ page: 2, size: 50, q: "alpha" });
  });

  it("createProject posts the payload", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 201, data: { id: "p1" } },
    ]);
    restore = r;
    const result = await createProject({
      team_id: "t1",
      name: "X",
      slug: "x",
    });
    expect(result.id).toBe("p1");
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/projects");
    expect(calls[0].data).toMatchObject({ team_id: "t1", name: "X", slug: "x" });
  });

  it("getProject hits the per-id route", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "p1" } },
    ]);
    restore = r;
    await getProject("p1");
    expect(calls[0].url).toBe("/v1/projects/p1");
    expect(calls[0].method).toBe("get");
  });

  it("updateProject PATCHes mutable fields", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "p1" } },
    ]);
    restore = r;
    await updateProject("p1", { name: "Renamed" });
    expect(calls[0].method).toBe("patch");
    expect(calls[0].data).toMatchObject({ name: "Renamed" });
  });

  it("archiveProject DELETEs", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 204, data: null },
    ]);
    restore = r;
    await archiveProject("p1");
    expect(calls[0].method).toBe("delete");
    expect(calls[0].url).toBe("/v1/projects/p1");
  });

  it("triggerScan POSTs to /scans with default kind=source", async () => {
    const { calls, restore: r } = installAdapter([
      {
        status: 202,
        data: {
          id: "scan-1",
          project_id: "p1",
          kind: "source",
          status: "queued",
          progress_percent: 0,
          current_step: null,
          started_at: null,
          completed_at: null,
          error_message: null,
          requested_by_user_id: null,
          celery_task_id: null,
          metadata: {},
          created_at: "2026-05-06T00:00:00Z",
          updated_at: "2026-05-06T00:00:00Z",
        },
      },
    ]);
    restore = r;
    await triggerScan("p1");
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/projects/p1/scans");
    expect(calls[0].data).toMatchObject({ kind: "source", metadata: {} });
  });

  it("getScan hits /v1/scans/{id}", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { id: "scan-1" } },
    ]);
    restore = r;
    await getScan("scan-1");
    expect(calls[0].url).toBe("/v1/scans/scan-1");
  });

  it("listScans hits /v1/projects/{id}/scans", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: { items: [], total: 0, page: 1, size: 20 } },
    ]);
    restore = r;
    await listScans("p1", { page: 1, size: 10 });
    expect(calls[0].url).toBe("/v1/projects/p1/scans");
    expect(calls[0].params).toMatchObject({ page: 1, size: 10 });
  });
});
