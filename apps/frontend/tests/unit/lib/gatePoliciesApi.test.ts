/**
 * gatePoliciesApi: the entity tag round trip and the problem-type matchers.
 *
 * Same adapter-stub approach as licensePoliciesApi.test.ts, so the real
 * wrappers run through the real interceptors (ProblemError mapping included)
 * without a network. What matters here is the headers: the read must hand the
 * tag back exactly as sent, and the write must turn "what I read" into the
 * precondition the backend checks.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { api } from "@/lib/api";
import {
  GATE_POLICY_PROBLEM_TYPES,
  getTeamGatePolicy,
  isGatePolicyConflict,
  isGatePolicyNotSet,
  upsertTeamGatePolicy,
} from "@/lib/gatePoliciesApi";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

interface Canned {
  status: number;
  data: unknown;
  headers?: Record<string, string>;
}

function installAdapter(responses: Canned[]): {
  calls: InternalAxiosRequestConfig[];
  restore: () => void;
} {
  const calls: InternalAxiosRequestConfig[] = [];
  const original = api.defaults.adapter;
  let i = 0;
  const adapter: AxiosAdapter = async (config) => {
    const canned = responses[i] ?? { status: 200, data: null };
    i += 1;
    calls.push(config);
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
  return { calls, restore: () => (api.defaults.adapter = original) };
}

function header(config: InternalAxiosRequestConfig, name: string): unknown {
  return config.headers?.get?.(name) ?? (config.headers as Record<string, unknown>)[name];
}

const TEAM_ID = "11111111-1111-1111-1111-111111111111";
const ROW = { id: "p1", team_id: TEAM_ID, name: "Release gate" };

function problem(status: number, type: string): ProblemError {
  return new ProblemError("gate policy", {
    status,
    title: "t",
    detail: "d",
    problem: { type, title: "t", status, detail: "d" },
  });
}

describe("gatePoliciesApi", () => {
  beforeEach(() => {
    useAuthStore.setState({ accessToken: "tok" });
  });
  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("returns the row together with the ETag exactly as the server sent it", async () => {
    const { restore } = installAdapter([
      { status: 200, data: ROW, headers: { etag: '"1729760000000001"' } },
    ]);
    const read = await getTeamGatePolicy(TEAM_ID);
    restore();
    expect(read.policy).toEqual(ROW);
    expect(read.etag).toBe('"1729760000000001"');
  });

  it("sends the tag it read as If-Match", async () => {
    const { calls, restore } = installAdapter([{ status: 200, data: ROW }]);
    await upsertTeamGatePolicy(TEAM_ID, { epss_threshold: 0.5 }, { etag: '"42"' });
    restore();
    expect(header(calls[0], "If-Match")).toBe('"42"');
    expect(header(calls[0], "If-None-Match")).toBeUndefined();
  });

  it("asks to create only when what it read was no row", async () => {
    const { calls, restore } = installAdapter([{ status: 200, data: ROW }]);
    await upsertTeamGatePolicy(TEAM_ID, { epss_threshold: 0.5 }, { etag: null });
    restore();
    expect(header(calls[0], "If-None-Match")).toBe("*");
    expect(header(calls[0], "If-Match")).toBeUndefined();
  });

  it("sends no precondition when the read carried no tag, rather than one that always fails", async () => {
    const { calls, restore } = installAdapter([{ status: 200, data: ROW }]);
    await upsertTeamGatePolicy(TEAM_ID, { epss_threshold: 0.5 }, { etag: "" });
    restore();
    expect(header(calls[0], "If-Match")).toBeUndefined();
    expect(header(calls[0], "If-None-Match")).toBeUndefined();
  });

  it("maps a refused save to a ProblemError the conflict matcher recognises", async () => {
    const { restore } = installAdapter([
      {
        status: 409,
        data: {
          type: GATE_POLICY_PROBLEM_TYPES.conflict,
          title: "Gate policy changed",
          status: 409,
          detail: "changed",
          instance: `/v1/gate-policies/teams/${TEAM_ID}`,
        },
        headers: { "content-type": "application/problem+json" },
      },
    ]);
    const error = await upsertTeamGatePolicy(TEAM_ID, {}, { etag: '"1"' }).catch(
      (e: unknown) => e,
    );
    restore();
    expect(isGatePolicyConflict(error)).toBe(true);
  });

  it("matches on the problem type, not the status alone", () => {
    expect(isGatePolicyNotSet(problem(404, GATE_POLICY_PROBLEM_TYPES.not_set))).toBe(true);
    expect(isGatePolicyNotSet(problem(404, "about:blank"))).toBe(false);
    expect(isGatePolicyNotSet(problem(409, GATE_POLICY_PROBLEM_TYPES.not_set))).toBe(false);
    expect(isGatePolicyConflict(problem(409, GATE_POLICY_PROBLEM_TYPES.conflict))).toBe(true);
    expect(isGatePolicyConflict(problem(409, "about:blank"))).toBe(false);
    expect(isGatePolicyConflict(problem(404, GATE_POLICY_PROBLEM_TYPES.conflict))).toBe(false);
    expect(isGatePolicyConflict(new Error("409"))).toBe(false);
  });
});
