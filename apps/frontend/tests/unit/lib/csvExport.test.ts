/**
 * csvExport: the two things every CSV download gets wrong on its own (B5).
 *
 * The blob error body is unwrapped by the real response interceptor, so that
 * case lives in blobErrorProblem.test.ts, which runs the real axios instance.
 * Mocking `api.get` here would skip the interceptor and test nothing.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { downloadCsvExport } from "@/lib/csvExport";
import * as download from "@/lib/download";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);

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
});
