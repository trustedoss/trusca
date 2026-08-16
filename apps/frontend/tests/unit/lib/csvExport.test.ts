/**
 * csvExport: the two things every CSV download gets wrong on its own (B5).
 */
import { AxiosError, AxiosHeaders } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { downloadCsvExport, problemFromBlobError } from "@/lib/csvExport";
import * as download from "@/lib/download";
import { ProblemError } from "@/lib/problem";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);

function axiosErrorWithBlob(status: number, body: unknown): AxiosError {
  const err = new AxiosError("Request failed");
  err.response = {
    status,
    statusText: "Payload Too Large",
    data: new Blob([JSON.stringify(body)], { type: "application/problem+json" }),
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  } as never;
  return err;
}

describe("downloadCsvExport", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockedGet.mockReset();
  });

  it("sends array filters as repeated keys", async () => {
    // Without this axios serialises them as `severity[]=a&severity[]=b`,
    // FastAPI sees no `severity` at all, and the file quietly carries rows
    // the screen was filtering out. That is the whole defect class this
    // feature exists to avoid.
    mockedGet.mockResolvedValue({
      data: new Blob(["a,b\n"]),
      headers: {},
    } as never);
    vi.spyOn(download, "triggerBlobDownload").mockImplementation(() => {});

    await downloadCsvExport("/v1/x/export.csv", { severity: ["critical"] }, "x.csv");

    const config = mockedGet.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(config.paramsSerializer).toEqual({ indexes: null });
    expect(config.responseType).toBe("blob");
  });

  it("names the file from the response header", async () => {
    mockedGet.mockResolvedValue({
      data: new Blob(["a,b\n"]),
      headers: { "content-disposition": 'attachment; filename="vulns_42.csv"' },
    } as never);
    const trigger = vi
      .spyOn(download, "triggerBlobDownload")
      .mockImplementation(() => {});

    await downloadCsvExport("/v1/x/export.csv", {}, "fallback.csv");

    expect(trigger).toHaveBeenCalledWith(expect.any(Blob), "vulns_42.csv");
  });

  it("falls back when a proxy strips the header", async () => {
    mockedGet.mockResolvedValue({ data: new Blob(["a\n"]), headers: {} } as never);
    const trigger = vi
      .spyOn(download, "triggerBlobDownload")
      .mockImplementation(() => {});

    await downloadCsvExport("/v1/x/export.csv", {}, "fallback.csv");

    expect(trigger).toHaveBeenCalledWith(expect.any(Blob), "fallback.csv");
  });

  it("turns a blob error body into a problem the UI can read", async () => {
    // Asking axios for a blob means the ERROR body arrives as a blob too.
    // The interceptor that normally builds a ProblemError sees an opaque
    // Blob, finds no extensions, and produces an error that says nothing,
    // so the reader who asked for a 200k-row export was told "something
    // went wrong" instead of "narrow the filter".
    mockedGet.mockRejectedValue(
      axiosErrorWithBlob(413, {
        type: "https://docs.trustedoss.io/errors/vulnerabilities-export-too-large",
        title: "Vulnerability Export Too Large",
        status: 413,
        detail: "narrow the filters and retry",
        vulnerabilities_export_too_large: true,
      }),
    );

    await expect(
      downloadCsvExport("/v1/x/export.csv", {}, "x.csv"),
    ).rejects.toSatisfy((err: unknown) => {
      expect(err).toBeInstanceOf(ProblemError);
      const problem = err as ProblemError;
      expect(problem.status).toBe(413);
      expect(problem.problem?.vulnerabilities_export_too_large).toBe(true);
      return true;
    });
  });
});

describe("problemFromBlobError", () => {
  it("leaves an already-parsed problem alone", async () => {
    const original = new ProblemError("x", {
      status: 500,
      title: "t",
      detail: "d",
      problem: null,
    });
    expect(await problemFromBlobError(original)).toBe(original);
  });

  it("leaves a non-axios error alone", async () => {
    const original = new Error("offline");
    expect(await problemFromBlobError(original)).toBe(original);
  });

  it("leaves a blob that is not JSON alone", async () => {
    // A proxy error page, a truncated body, an empty 502. The original
    // error is the more honest answer than a problem invented from nothing.
    const err = new AxiosError("Bad gateway");
    err.response = {
      status: 502,
      statusText: "Bad Gateway",
      data: new Blob(["<html>nginx</html>"]),
      headers: new AxiosHeaders(),
      config: { headers: new AxiosHeaders() },
    } as never;

    expect(await problemFromBlobError(err)).toBe(err);
  });
});
