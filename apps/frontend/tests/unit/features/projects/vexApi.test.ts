/**
 * vexApi — unit tests (v2.1 A3).
 *
 * Exercises the real axios request/response interceptors on the shared `api`
 * instance via a stubbed adapter:
 *   - downloadVex hits GET /v1/projects/{id}/vex?format=… as a blob with the
 *     bearer header, prefers the Content-Disposition filename, and falls back
 *     to a client-built `<name>-vex-<format>.json`.
 *   - importVex POSTs a multipart body to /v1/projects/{id}/vex/import.
 *   - 4xx responses surface as ProblemError.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { downloadVex, importVex } from "@/features/projects/api/vexApi";
import { api } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

interface Recorded {
  method: string;
  url: string;
  params: unknown;
  responseType: string | undefined;
  authorization: string | undefined;
  data: unknown;
}

function installAdapter(
  responses: Array<{
    status: number;
    data: unknown;
    headers?: Record<string, string>;
  }>,
): { calls: Recorded[]; restore: () => void } {
  const calls: Recorded[] = [];
  const original = api.defaults.adapter;
  let i = 0;
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
    const canned = responses[i] ?? { status: 200, data: null };
    i += 1;
    const headers = config.headers as Record<string, string> | undefined;
    calls.push({
      method: (config.method ?? "get").toLowerCase(),
      url: config.url ?? "",
      params: config.params,
      responseType: config.responseType,
      authorization: headers?.Authorization,
      data: config.data,
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
  return { calls, restore: () => (api.defaults.adapter = original) };
}

describe("vexApi", () => {
  beforeEach(() => useAuthStore.getState().setAccessToken("test-token"));
  afterEach(() => useAuthStore.getState().reset());

  describe("downloadVex", () => {
    it("requests the VEX endpoint as a blob with the format param + bearer header", async () => {
      const { calls, restore } = installAdapter([
        {
          status: 200,
          data: new Blob(["{}"], { type: "application/json" }),
          headers: {
            "content-disposition": 'attachment; filename="acme-vex.json"',
          },
        },
      ]);
      try {
        const result = await downloadVex("proj-1", "openvex", "Acme");
        expect(calls[0].method).toBe("get");
        expect(calls[0].url).toBe("/v1/projects/proj-1/vex");
        expect(calls[0].params).toEqual({ format: "openvex" });
        expect(calls[0].responseType).toBe("blob");
        expect(calls[0].authorization).toBe("Bearer test-token");
        expect(result.filename).toBe("acme-vex.json");
        expect(result.blob.type).toBe("application/json");
      } finally {
        restore();
      }
    });

    it("falls back to <name>-vex-<format>.json when no Content-Disposition", async () => {
      const { restore } = installAdapter([
        { status: 200, data: new Blob(["{}"]), headers: {} },
      ]);
      try {
        const result = await downloadVex("proj-1", "cyclonedx", "My Project!");
        expect(result.filename).toBe("My-Project-vex-cyclonedx.json");
      } finally {
        restore();
      }
    });

    it("maps a 404 into a ProblemError", async () => {
      const { restore } = installAdapter([{ status: 404, data: null }]);
      try {
        await expect(
          downloadVex("missing", "openvex"),
        ).rejects.toBeInstanceOf(ProblemError);
      } finally {
        restore();
      }
    });
  });

  describe("importVex", () => {
    it("posts a multipart body to the import endpoint and returns the summary", async () => {
      const { calls, restore } = installAdapter([
        {
          status: 200,
          data: {
            format: "openvex",
            matched: 3,
            applied: 2,
            skipped: 1,
            errors: [],
          },
          headers: {},
        },
      ]);
      try {
        const file = new File(['{"@context":"openvex"}'], "vex.json", {
          type: "application/json",
        });
        const result = await importVex("proj-1", file);
        expect(calls[0].method).toBe("post");
        expect(calls[0].url).toBe("/v1/projects/proj-1/vex/import");
        expect(calls[0].data).toBeInstanceOf(FormData);
        expect((calls[0].data as FormData).get("upload")).toBeInstanceOf(File);
        expect(result.applied).toBe(2);
        expect(result.format).toBe("openvex");
      } finally {
        restore();
      }
    });

    it("maps a 403 into a ProblemError", async () => {
      const { restore } = installAdapter([{ status: 403, data: null }]);
      try {
        const file = new File(["{}"], "vex.json", {
          type: "application/json",
        });
        await expect(importVex("proj-1", file)).rejects.toBeInstanceOf(
          ProblemError,
        );
      } finally {
        restore();
      }
    });
  });
});
