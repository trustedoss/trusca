/**
 * licensePoliciesApi — unit tests (v2.2 c3).
 *
 * Uses the adapter-stub trick from approvalsApi.test.ts: install a canned
 * adapter on the shared axios instance so the real wrapper functions run
 * through the real interceptors (Bearer header, ProblemError mapping) without
 * touching a network. Asserts URL, method, body, and query params for every
 * endpoint, plus the local draft helpers.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/lib/api";
import {
  defaultCompoundStrategy,
  deleteTeamPolicy,
  emptyPolicyDraft,
  getOrgPolicy,
  getTeamPolicy,
  listLicensePolicies,
  upsertOrgPolicy,
  upsertTeamPolicy,
  type LicensePolicyOut,
} from "@/lib/licensePoliciesApi";
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
  return { calls, restore: () => (api.defaults.adapter = original) };
}

const TEAM_ID = "11111111-1111-1111-1111-111111111111";
const ORG_ID = "99999999-9999-9999-9999-999999999999";

function policy(over: Partial<LicensePolicyOut> = {}): LicensePolicyOut {
  return {
    id: "p1",
    organization_id: ORG_ID,
    team_id: TEAM_ID,
    name: "Eng",
    category_overrides: {},
    license_exceptions: [],
    unknown_license_category: "conditional",
    compound_operator_strategy: defaultCompoundStrategy(),
    enabled: true,
    created_by_user_id: null,
    created_at: "2026-05-24T00:00:00Z",
    updated_at: "2026-05-24T00:00:00Z",
    ...over,
  };
}

describe("licensePoliciesApi", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: "tok" });
  });
  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("lists policies with org/team/page query params", async () => {
    const { calls, restore } = installAdapter([
      { status: 200, data: { items: [], total: 0, page: 1, page_size: 50 } },
    ]);
    const page = await listLicensePolicies({
      organization_id: ORG_ID,
      team_id: TEAM_ID,
      page: 2,
      page_size: 25,
    });
    restore();
    expect(page.total).toBe(0);
    expect(calls[0].method).toBe("get");
    expect(calls[0].url).toBe("/v1/license-policies");
    expect(calls[0].params).toMatchObject({
      organization_id: ORG_ID,
      team_id: TEAM_ID,
      page: 2,
      page_size: 25,
    });
  });

  it("reads the effective team policy", async () => {
    const { calls, restore } = installAdapter([{ status: 200, data: policy() }]);
    const result = await getTeamPolicy(TEAM_ID);
    restore();
    expect(result.team_id).toBe(TEAM_ID);
    expect(calls[0].url).toBe(`/v1/license-policies/teams/${TEAM_ID}`);
  });

  it("upserts the team policy via PUT with the payload body", async () => {
    const { calls, restore } = installAdapter([{ status: 200, data: policy() }]);
    await upsertTeamPolicy(TEAM_ID, {
      ...emptyPolicyDraft(),
      category_overrides: { "MPL-2.0": "forbidden" },
    });
    restore();
    expect(calls[0].method).toBe("put");
    expect(calls[0].url).toBe(`/v1/license-policies/teams/${TEAM_ID}`);
    expect(calls[0].data).toMatchObject({
      category_overrides: { "MPL-2.0": "forbidden" },
      enabled: true,
    });
  });

  it("deletes (resets) the team policy via DELETE", async () => {
    const { calls, restore } = installAdapter([{ status: 204, data: null }]);
    await deleteTeamPolicy(TEAM_ID);
    restore();
    expect(calls[0].method).toBe("delete");
    expect(calls[0].url).toBe(`/v1/license-policies/teams/${TEAM_ID}`);
  });

  it("reads and upserts the org-default policy", async () => {
    const { calls, restore } = installAdapter([
      { status: 200, data: policy({ team_id: null }) },
      { status: 200, data: policy({ team_id: null }) },
    ]);
    const read = await getOrgPolicy(ORG_ID);
    await upsertOrgPolicy(ORG_ID, emptyPolicyDraft());
    restore();
    expect(read.team_id).toBeNull();
    expect(calls[0].url).toBe(`/v1/license-policies/org/${ORG_ID}`);
    expect(calls[1].method).toBe("put");
    expect(calls[1].url).toBe(`/v1/license-policies/org/${ORG_ID}`);
  });

  it("surfaces a 422 as a ProblemError", async () => {
    const { restore } = installAdapter([
      {
        status: 422,
        data: { type: "about:blank", title: "Unprocessable", status: 422, detail: "bad" },
      },
    ]);
    await expect(upsertTeamPolicy(TEAM_ID, emptyPolicyDraft())).rejects.toThrow();
    restore();
  });

  it("emptyPolicyDraft is a blank, enabled, conservative draft", () => {
    const draft = emptyPolicyDraft();
    expect(draft.enabled).toBe(true);
    expect(draft.category_overrides).toEqual({});
    expect(draft.license_exceptions).toEqual([]);
    expect(draft.unknown_license_category).toBe("conditional");
    expect(draft.compound_operator_strategy).toEqual({
      AND: "most_restrictive",
      OR: "least_restrictive",
      WITH: "most_restrictive",
    });
  });
});
