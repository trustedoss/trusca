/**
 * vulnReportApi — unit tests (G2 frontend).
 *
 * We stub the axios adapter on the shared `api` instance so the wrapper
 * exercises the real request interceptor (Bearer header) and response
 * interceptor (ProblemError mapping). The fetch must:
 *   - hit GET /v1/projects/{id}/vulnerability-report.pdf with responseType blob,
 *   - carry the Authorization: Bearer header,
 *   - prefer the Content-Disposition filename,
 *   - fall back to a client-built name when the header is absent,
 *   - always return an application/pdf Blob,
 *   - surface non-2xx as a ProblemError.
 */
import type {
  AxiosAdapter,
  AxiosResponse,
  InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fetchVulnerabilityReportPdf } from "@/features/projects/api/vulnReportApi";
import { api } from "@/lib/api";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";

interface Recorded {
  method: string;
  url: string;
  responseType: string | undefined;
  authorization: string | undefined;
}

function installAdapter(
  responses: Array<{ status: number; data: unknown; headers?: Record<string, string> }>,
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
      responseType: config.responseType,
      authorization: headers?.Authorization,
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

describe("vulnReportApi", () => {
  beforeEach(() => {
    useAuthStore.getState().setAccessToken("test-token");
  });
  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("requests the PDF endpoint as a blob with the bearer header", async () => {
    const { calls, restore } = installAdapter([
      {
        status: 200,
        data: new Blob(["%PDF-1.7"], { type: "application/pdf" }),
        headers: {
          "content-disposition":
            'attachment; filename="vulnerability-report-acme.pdf"',
        },
      },
    ]);
    try {
      const result = await fetchVulnerabilityReportPdf("proj-1", "Acme");
      expect(calls[0].method).toBe("get");
      expect(calls[0].url).toBe("/v1/projects/proj-1/vulnerability-report.pdf");
      expect(calls[0].responseType).toBe("blob");
      expect(calls[0].authorization).toBe("Bearer test-token");
      expect(result.filename).toBe("vulnerability-report-acme.pdf");
      expect(result.blob.type).toBe("application/pdf");
    } finally {
      restore();
    }
  });

  it("falls back to a client-built filename when no Content-Disposition", async () => {
    const { restore } = installAdapter([
      {
        status: 200,
        data: new Blob(["%PDF-1.7"], { type: "application/pdf" }),
        headers: {},
      },
    ]);
    try {
      const result = await fetchVulnerabilityReportPdf("proj-1", "My Project!");
      // Spaces / punctuation collapse to hyphens (safeFilenameToken).
      expect(result.filename).toBe("vulnerability-report-My-Project.pdf");
    } finally {
      restore();
    }
  });

  it("uses the project id when no name is supplied", async () => {
    const { restore } = installAdapter([
      { status: 200, data: new Blob(["%PDF-1.7"]), headers: {} },
    ]);
    try {
      const result = await fetchVulnerabilityReportPdf("proj-99");
      expect(result.filename).toBe("vulnerability-report-proj-99.pdf");
    } finally {
      restore();
    }
  });

  it("maps a 404 into a ProblemError", async () => {
    const { restore } = installAdapter([{ status: 404, data: null }]);
    try {
      await expect(
        fetchVulnerabilityReportPdf("missing", "X"),
      ).rejects.toBeInstanceOf(ProblemError);
    } finally {
      restore();
    }
  });
});
