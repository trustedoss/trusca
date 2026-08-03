/**
 * sourceTreeApi — unit tests (G3.3).
 *
 * Uses the adapter-stub trick (approvalsApi.test.ts): install a canned adapter
 * on the shared axios `api` instance so calls run through the real interceptors
 * (Bearer header attach, ProblemError mapping) without a network. Asserts the
 * URL, query-param construction (path / page / size / scan_id), and that the
 * bearer token is attached + a 404 maps to a ProblemError.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  getSourceFile,
  getSourceTree,
} from "@/features/projects/api/sourceTreeApi";
import { api } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

interface Recorded {
  method: string;
  url: string;
  params: Record<string, unknown>;
  headers: Record<string, unknown>;
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
      params: (config.params as Record<string, unknown>) ?? {},
      headers: (config.headers as unknown as Record<string, unknown>) ?? {},
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

const TREE_PAGE = {
  scan_id: "scan-1",
  path: "src",
  entries: [],
  total: 0,
  page: 1,
  size: 100,
};

const FILE = {
  scan_id: "scan-1",
  path: "src/main.py",
  byte_size: 10,
  truncated: false,
  encoding: "utf-8" as const,
  content: "print(1)",
  license_matches: [],
};

describe("sourceTreeApi", () => {
  let restore: () => void;

  beforeEach(() => {
    useAuthStore.getState().setAccessToken("test-token");
  });
  afterEach(() => {
    restore?.();
    useAuthStore.getState().reset();
  });

  it("getSourceTree hits the tree endpoint and attaches the bearer token", async () => {
    const stub = installAdapter([{ status: 200, data: TREE_PAGE }]);
    restore = stub.restore;
    const out = await getSourceTree("proj-1", { path: "src" });
    expect(out.scan_id).toBe("scan-1");
    expect(stub.calls[0].url).toBe("/v1/projects/proj-1/source-tree");
    expect(stub.calls[0].params).toEqual({ path: "src" });
    expect(stub.calls[0].headers.Authorization).toBe("Bearer test-token");
  });

  it("getSourceTree omits the empty root path + maps scanId → scan_id", async () => {
    const stub = installAdapter([{ status: 200, data: TREE_PAGE }]);
    restore = stub.restore;
    await getSourceTree("proj-1", {
      path: "",
      page: 2,
      size: 250,
      scanId: "scan-9",
    });
    expect(stub.calls[0].params).toEqual({
      page: 2,
      size: 250,
      scan_id: "scan-9",
    });
    expect(stub.calls[0].params).not.toHaveProperty("path");
  });

  it("getSourceFile hits the file endpoint with the path param", async () => {
    const stub = installAdapter([{ status: 200, data: FILE }]);
    restore = stub.restore;
    const out = await getSourceFile("proj-1", { path: "src/main.py" });
    expect(out.encoding).toBe("utf-8");
    expect(stub.calls[0].url).toBe("/v1/projects/proj-1/source-file");
    expect(stub.calls[0].params).toEqual({ path: "src/main.py" });
  });

  it("getSourceFile forwards scanId as scan_id when provided", async () => {
    const stub = installAdapter([{ status: 200, data: FILE }]);
    restore = stub.restore;
    await getSourceFile("proj-1", { path: "x", scanId: "scan-7" });
    expect(stub.calls[0].params).toEqual({ path: "x", scan_id: "scan-7" });
  });

  it("maps a 404 (no preserved source) to a ProblemError", async () => {
    const stub = installAdapter([
      {
        status: 404,
        data: {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: "no preserved source",
        },
      },
    ]);
    restore = stub.restore;
    await expect(getSourceTree("proj-1")).rejects.toBeInstanceOf(ProblemError);
  });
});
