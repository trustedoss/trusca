/**
 * vulnerabilitiesApi.listUpgradeClusters — wire layer tests (W9-#53).
 *
 * Direct unit tests for the axios wrapper: the endpoint path, the optional
 * `scanId` → `scan_id` query mapping (threaded snapshot anchor), and that the
 * param is omitted when unset. Mirrors the licensesApi / projectDetailApi wire
 * test convention (mock `@/lib/api`, assert the `get` call shape).
 */
import type { AxiosInstance } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => {
  const get = vi.fn();
  return { api: { get } as unknown as AxiosInstance };
});

import { api } from "@/lib/api";
import {
  listUpgradeClusters,
  type UpgradeClusterListResponse,
} from "@/features/projects/api/vulnerabilitiesApi";

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

const EMPTY: UpgradeClusterListResponse = {
  scan_id: null,
  total_findings: 0,
  clusters: [],
};

describe("vulnerabilitiesApi.listUpgradeClusters", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({ data: EMPTY });
  });

  it("hits the upgrade-clusters endpoint with no query by default", async () => {
    const result = await listUpgradeClusters("proj-1");
    expect(mockedGet).toHaveBeenCalledWith(
      "/v1/projects/proj-1/vulnerabilities/upgrade-clusters",
      { params: {} },
    );
    expect(result).toEqual(EMPTY);
  });

  it("threads scanId as the scan_id snapshot anchor", async () => {
    await listUpgradeClusters("proj-1", { scanId: "scan-42" });
    const call = mockedGet.mock.calls[0]!;
    expect(call[1].params).toEqual({ scan_id: "scan-42" });
  });

  it("omits scan_id when scanId is empty or absent", async () => {
    await listUpgradeClusters("proj-1", { scanId: "" });
    const call = mockedGet.mock.calls[0]!;
    expect(call[1].params).not.toHaveProperty("scan_id");
  });
});
