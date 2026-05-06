/**
 * projectDetailApi — wire layer tests (PR #10).
 *
 * Direct unit tests for the axios wrappers, ensuring the URL paths match the
 * backend contract and the array filters serialize as repeated query params
 * (FastAPI `list[str]` convention).
 */
import type { AxiosInstance } from "axios";
import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@/lib/api", () => {
  const get = vi.fn();
  return { api: { get } as unknown as AxiosInstance };
});

import { api } from "@/lib/api";
import {
  getComponent,
  getProjectOverview,
  listProjectComponents,
} from "@/features/projects/api/projectDetailApi";

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

describe("projectDetailApi", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({ data: {} });
  });

  it("getProjectOverview hits /v1/projects/{id}/overview", async () => {
    await getProjectOverview("proj-1");
    expect(mockedGet).toHaveBeenCalledWith("/v1/projects/proj-1/overview");
  });

  it("listProjectComponents passes pagination + sort params", async () => {
    await listProjectComponents("proj-1", {
      limit: 50,
      offset: 100,
      sort: "severity",
      order: "desc",
    });
    expect(mockedGet).toHaveBeenCalledWith(
      "/v1/projects/proj-1/components",
      expect.objectContaining({
        params: expect.objectContaining({
          limit: 50,
          offset: 100,
          sort: "severity",
          order: "desc",
        }),
      }),
    );
  });

  it("listProjectComponents includes severity / license_category arrays only when non-empty", async () => {
    await listProjectComponents("proj-1", {
      severity: ["critical", "high"],
      license_category: [],
    });
    const call = mockedGet.mock.calls[0]!;
    const params = call[1].params;
    expect(params.severity).toEqual(["critical", "high"]);
    expect(params).not.toHaveProperty("license_category");
  });

  it("listProjectComponents trims empty searches before sending", async () => {
    await listProjectComponents("proj-1", { search: "" });
    const call = mockedGet.mock.calls[0]!;
    expect(call[1].params).not.toHaveProperty("search");
  });

  it("getComponent hits /v1/components/{id}", async () => {
    await getComponent("alpha-id");
    expect(mockedGet).toHaveBeenCalledWith("/v1/components/alpha-id");
  });
});
