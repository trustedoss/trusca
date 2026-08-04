/**
 * fetchProjectNotice — wire layer tests (release-snapshot anchor).
 *
 * The NOTICE is the attribution artefact a release ships with, so a tab pinned
 * to an older release must download THAT release's document rather than the
 * latest scan's. These tests pin the wire contract: `scanId` maps to the
 * backend's `scan_id` param and is omitted when unset. Mirrors the licensesApi
 * wire-test convention.
 */
import type { AxiosInstance } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => {
  const get = vi.fn();
  return { api: { get } as unknown as AxiosInstance };
});

import { api } from "@/lib/api";
import { fetchProjectNotice } from "@/features/projects/api/obligationsApi";

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

describe("obligationsApi.fetchProjectNotice", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({ data: "NOTICE body", headers: {} });
  });

  it("hits /v1/projects/{id}/notice with the requested format", async () => {
    await fetchProjectNotice("proj-1", { format: "markdown" });
    const call = mockedGet.mock.calls[0]!;
    expect(call[0]).toBe("/v1/projects/proj-1/notice");
    expect(call[1].params).toMatchObject({ format: "markdown" });
  });

  it("forwards scanId as the scan_id snapshot anchor", async () => {
    await fetchProjectNotice("proj-1", {
      format: "text",
      download: true,
      scanId: "scan-42",
    });
    const call = mockedGet.mock.calls[0]!;
    expect(call[1].params).toMatchObject({
      format: "text",
      download: true,
      scan_id: "scan-42",
    });
  });

  it("omits scan_id when unpinned, so the backend keeps its latest-succeeded default", async () => {
    await fetchProjectNotice("proj-1");
    const call = mockedGet.mock.calls[0]!;
    expect(call[1].params).not.toHaveProperty("scan_id");
  });

  it("omits scan_id for an empty scanId rather than sending a blank anchor", async () => {
    await fetchProjectNotice("proj-1", { scanId: "" });
    const call = mockedGet.mock.calls[0]!;
    expect(call[1].params).not.toHaveProperty("scan_id");
  });
});
