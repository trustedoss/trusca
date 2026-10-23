/**
 * Blob downloads surface the server's RFC 7807 problem, through the real
 * axios instance and its real response interceptor.
 *
 * A request made with `responseType: "blob"` gets its error body as a Blob.
 * The interceptor used to parse that Blob as if it were JSON, find nothing,
 * and throw a ProblemError whose detail was only the status line; the
 * csvExport helper that tried to recover the body afterwards saw an
 * existing ProblemError and returned it untouched. Its unit test fed it a
 * raw AxiosError, a shape that never reaches callers in production, so the
 * defect stayed invisible. Here only the transport adapter is stubbed: the
 * error is built the way axios builds it, and every blob download caller
 * goes through the interceptor exactly as it does in the browser.
 */
import {
  AxiosError,
  type AxiosAdapter,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from "axios";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { downloadAdminAuditCsv } from "@/features/admin/audit/api/adminAuditApi";
import { downloadBackup } from "@/features/admin/api/adminBackupsApi";
import { getSourceFileRaw } from "@/features/projects/api/sourceTreeApi";
import { downloadVex } from "@/features/projects/api/vexApi";
import {
  fetchVulnerabilityReportPdf,
  fetchVulnerabilityReportXlsx,
} from "@/features/projects/api/vulnReportApi";
import { api } from "@/lib/api";
import { downloadCsvExport } from "@/lib/csvExport";
import { ProblemError } from "@/lib/problem";
import { downloadSbom, downloadSbomSignatureArtifact } from "@/lib/projectsApi";
import { useAuthStore } from "@/stores/authStore";

const PROBLEM = {
  type: "https://docs.trustedoss.io/errors/export-too-large",
  title: "Export Too Large",
  status: 413,
  detail: "narrow the filters and retry",
  vulnerabilities_export_too_large: true,
};

/**
 * Answer every request with `status` and `body`, rejecting the way axios'
 * own `settle` does for a non-2xx status: an AxiosError carrying the
 * response. The body is whatever the browser would hand a blob request.
 */
function respondWith(status: number, body: Blob): AxiosAdapter {
  return async (config: InternalAxiosRequestConfig) => {
    const response: AxiosResponse = {
      data: body,
      status,
      statusText: status === 413 ? "Payload Too Large" : "Bad Gateway",
      headers: {},
      config,
      request: {},
    };
    throw new AxiosError(
      `Request failed with status code ${status}`,
      AxiosError.ERR_BAD_REQUEST,
      config,
      {},
      response,
    );
  };
}

function problemBlob(body: unknown = PROBLEM): Blob {
  return new Blob([JSON.stringify(body)], { type: "application/problem+json" });
}

async function rejection(promise: Promise<unknown>): Promise<ProblemError> {
  try {
    await promise;
  } catch (err) {
    expect(err).toBeInstanceOf(ProblemError);
    return err as ProblemError;
  }
  throw new Error("expected the download to reject");
}

const originalAdapter = api.defaults.adapter;

beforeEach(() => {
  useAuthStore.setState({
    user: null,
    accessToken: "token",
    status: "authenticated",
    isAuthenticated: true,
  });
});

afterEach(() => {
  api.defaults.adapter = originalAdapter;
  vi.restoreAllMocks();
});

const CALLERS: Array<[string, () => Promise<unknown>]> = [
  ["csvExport.downloadCsvExport", () => downloadCsvExport("/v1/x/export.csv", {}, "x.csv")],
  ["vexApi.downloadVex", () => downloadVex("p1", "openvex")],
  ["vulnReportApi.fetchVulnerabilityReportPdf", () => fetchVulnerabilityReportPdf("p1")],
  ["vulnReportApi.fetchVulnerabilityReportXlsx", () => fetchVulnerabilityReportXlsx("p1")],
  ["adminAuditApi.downloadAdminAuditCsv", () => downloadAdminAuditCsv()],
  ["adminBackupsApi.downloadBackup", () => downloadBackup("backup-1")],
  ["sourceTreeApi.getSourceFileRaw", () => getSourceFileRaw("p1", { path: "a/b.c" })],
  ["projectsApi.downloadSbom", () => downloadSbom("p1", "cyclonedx-json")],
  [
    "projectsApi.downloadSbomSignatureArtifact",
    () => downloadSbomSignatureArtifact("p1", "bundle"),
  ],
];

describe("blob download errors carry the server's problem", () => {
  it.each(CALLERS)("%s", async (_name, call) => {
    api.defaults.adapter = respondWith(413, problemBlob());

    const err = await rejection(call());

    expect(err.status).toBe(413);
    expect(err.title).toBe("Export Too Large");
    expect(err.detail).toBe("narrow the filters and retry");
    expect(err.message).toBe("narrow the filters and retry");
    expect(err.problem?.vulnerabilities_export_too_large).toBe(true);
  });

  it("keeps the status line when the blob is not JSON (a proxy error page)", async () => {
    api.defaults.adapter = respondWith(502, new Blob(["<html>bad gateway</html>"]));

    const err = await rejection(downloadVex("p1", "openvex"));

    expect(err.status).toBe(502);
    expect(err.problem).toBeNull();
    expect(err.detail).toBe("Bad Gateway");
  });
});
