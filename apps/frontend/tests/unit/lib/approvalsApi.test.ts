/**
 * approvalsApi — unit tests (Phase 4 PR #15).
 *
 * Uses the adapter-stub trick from adminUsersApi.test.ts: install a canned
 * adapter on the shared axios instance so calls run through the real
 * interceptors (Bearer header, ProblemError mapping) without touching a
 * network. Asserts URL, method, body, query params, and ETag header handling
 * for every wrapper function.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  createApproval,
  deleteApproval,
  getApproval,
  listApprovals,
  transitionApproval,
} from "@/lib/approvalsApi";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

// ---------------------------------------------------------------------------
// Shared adapter stub
// ---------------------------------------------------------------------------

interface Recorded {
  method: string;
  url: string;
  data: unknown;
  params: Record<string, unknown>;
  headers: Record<string, unknown>;
}

function installAdapter(
  responses: Array<{ status: number; data: unknown; headers?: Record<string, string> }>,
): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = [];
  const original = api.defaults.adapter;
  let i = 0;
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    const canned = responses[i] ?? { status: 200, data: null, headers: {} };
    i += 1;
    calls.push({
      method: (config.method ?? "get").toLowerCase(),
      url: config.url ?? "",
      data: config.data ? JSON.parse(config.data as string) : undefined,
      params: (config.params as Record<string, unknown>) ?? {},
      headers: (config.headers as unknown as Record<string, unknown>) ?? {},
    });
    const response: AxiosResponse = {
      data: canned.data,
      status: canned.status,
      statusText: "",
      headers: canned.headers ?? {},
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

// ---------------------------------------------------------------------------
// Fixture
// ---------------------------------------------------------------------------

const APPROVAL = {
  id: "aaaaaaaa-0000-0000-0000-000000000001",
  component_id: "comp-0001",
  project_id: "proj-0001",
  team_id: "team-0001",
  requested_by_user_id: "user-0001",
  requested_at: "2026-05-01T10:00:00Z",
  status: "pending" as const,
  decided_by_user_id: null,
  decided_at: null,
  decision_note: null,
  version: 1,
};

const PAGE = {
  items: [APPROVAL],
  total: 1,
  page: 1,
  page_size: 25,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("approvalsApi", () => {
  let restore: () => void = () => {};

  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: "tok-test",
      status: "authenticated",
      isAuthenticated: true,
    });
  });

  afterEach(() => {
    restore();
  });

  it("listApprovals — GET /v1/approvals without filters", async () => {
    const { calls, restore: r } = installAdapter([{ status: 200, data: PAGE }]);
    restore = r;

    const result = await listApprovals({});
    expect(result.items).toHaveLength(1);
    expect(calls[0].method).toBe("get");
    expect(calls[0].url).toBe("/v1/approvals");
  });

  it("listApprovals — forwards status filter (omits 'all' sentinel)", async () => {
    const { calls, restore: r } = installAdapter([{ status: 200, data: PAGE }]);
    restore = r;

    await listApprovals({ status: "pending", page: 2, page_size: 10 });
    expect(calls[0].params).toMatchObject({
      status: "pending",
      page: 2,
      page_size: 10,
    });
  });

  it("listApprovals — omits status param when set to 'all'", async () => {
    const { calls, restore: r } = installAdapter([{ status: 200, data: PAGE }]);
    restore = r;

    await listApprovals({ status: "all" });
    expect(calls[0].params.status).toBeUndefined();
  });

  it("getApproval — GET /v1/approvals/{id} and strips ETag quotes", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 200, data: APPROVAL, headers: { etag: '"3"' } },
    ]);
    restore = r;

    const { approval, etag } = await getApproval(APPROVAL.id);
    expect(approval.id).toBe(APPROVAL.id);
    // Quotes stripped from ETag value.
    expect(etag).toBe("3");
    expect(calls[0].url).toBe(`/v1/approvals/${APPROVAL.id}`);
  });

  it("getApproval — handles ETag without surrounding quotes", async () => {
    const { restore: r } = installAdapter([
      { status: 200, data: APPROVAL, headers: { etag: "5" } },
    ]);
    restore = r;

    const { etag } = await getApproval(APPROVAL.id);
    expect(etag).toBe("5");
  });

  it("createApproval — POST /v1/approvals with correct body", async () => {
    const { calls, restore: r } = installAdapter([
      { status: 201, data: APPROVAL },
    ]);
    restore = r;

    const result = await createApproval({
      component_id: "comp-0001",
      project_id: "proj-0001",
    });
    expect(result.id).toBe(APPROVAL.id);
    expect(calls[0].method).toBe("post");
    expect(calls[0].url).toBe("/v1/approvals");
    expect(calls[0].data).toMatchObject({
      component_id: "comp-0001",
      project_id: "proj-0001",
    });
  });

  it("transitionApproval — PATCH with If-Match header (re-quoted)", async () => {
    const updated = { ...APPROVAL, status: "under_review" as const, version: 2 };
    const { calls, restore: r } = installAdapter([{ status: 200, data: updated }]);
    restore = r;

    const result = await transitionApproval(
      APPROVAL.id,
      "under_review",
      "1",
      "LGTM",
    );
    expect(result.status).toBe("under_review");
    expect(calls[0].method).toBe("patch");
    expect(calls[0].url).toBe(`/v1/approvals/${APPROVAL.id}/transition`);
    expect(calls[0].data).toMatchObject({ action: "under_review", decision_note: "LGTM" });
    // If-Match must wrap the etag in quotes.
    expect((calls[0].headers as Record<string, string>)["If-Match"]).toBe('"1"');
  });

  it("transitionApproval — sends null decision_note when not provided", async () => {
    const updated = { ...APPROVAL, status: "approved" as const, version: 2 };
    const { calls, restore: r } = installAdapter([{ status: 200, data: updated }]);
    restore = r;

    await transitionApproval(APPROVAL.id, "approved", "2");
    expect(calls[0].data).toMatchObject({ action: "approved", decision_note: null });
  });

  it("deleteApproval — DELETE /v1/approvals/{id}", async () => {
    const { calls, restore: r } = installAdapter([{ status: 204, data: null }]);
    restore = r;

    await deleteApproval(APPROVAL.id);
    expect(calls[0].method).toBe("delete");
    expect(calls[0].url).toBe(`/v1/approvals/${APPROVAL.id}`);
  });
});
