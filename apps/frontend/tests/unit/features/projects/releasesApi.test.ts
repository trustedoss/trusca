/**
 * releasesApi + releaseLabel — wire/helper unit tests (feature #28 Phase 1).
 *
 * Direct tests for the axios wrapper (URL path + pagination params) and the
 * shared release-label fallback chain (name → date → em-dash). Mirrors the
 * style of projectDetailApi.test.ts.
 */
import type { AxiosInstance } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => {
  const get = vi.fn();
  return { api: { get } as unknown as AxiosInstance };
});

import { api } from "@/lib/api";
import {
  listProjectReleases,
  type ReleaseSnapshot,
} from "@/features/projects/api/releasesApi";
import { releaseLabel } from "@/features/projects/lib/releaseLabel";

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

function snapshot(overrides: Partial<ReleaseSnapshot> = {}): ReleaseSnapshot {
  return {
    scan_id: "scan-1",
    release: null,
    created_at: "2026-05-22T10:00:00Z",
    risk_score: 50,
    severity_summary: { critical: 1, high: 0, medium: 0, low: 0 },
    gate_status: "fail",
    component_count: 5,
    ...overrides,
  };
}

describe("releasesApi", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({
      data: { items: [], total: 0, page: 1, size: 20 },
    });
  });

  it("hits /v1/projects/{id}/releases and returns the body", async () => {
    mockedGet.mockResolvedValueOnce({
      data: {
        items: [snapshot()],
        total: 1,
        page: 1,
        size: 20,
      },
    });
    const result = await listProjectReleases("proj-1");
    expect(mockedGet).toHaveBeenCalledWith(
      "/v1/projects/proj-1/releases",
      expect.objectContaining({ params: {} }),
    );
    expect(result.total).toBe(1);
    expect(result.items[0]?.scan_id).toBe("scan-1");
  });

  it("forwards page + size pagination params", async () => {
    await listProjectReleases("proj-1", { page: 3, size: 25 });
    expect(mockedGet).toHaveBeenCalledWith(
      "/v1/projects/proj-1/releases",
      expect.objectContaining({ params: { page: 3, size: 25 } }),
    );
  });
});

describe("releaseLabel", () => {
  it("returns the release name when present", () => {
    expect(releaseLabel(snapshot({ release: "v1.2.3" }), "en")).toBe("v1.2.3");
  });

  it("trims whitespace-only release names and falls back to the date", () => {
    const label = releaseLabel(
      snapshot({ release: "   ", created_at: "2026-05-22T10:00:00Z" }),
      "en",
    );
    expect(label).not.toBe("   ");
    expect(label).toContain("2026");
  });

  it("formats the created date when there is no release name", () => {
    const label = releaseLabel(
      snapshot({ release: null, created_at: "2026-05-22T10:00:00Z" }),
      "en",
    );
    expect(label).toContain("2026");
  });

  it("returns an em-dash when the timestamp is unparseable", () => {
    expect(
      releaseLabel(snapshot({ release: null, created_at: "not-a-date" }), "en"),
    ).toBe("—");
  });
});
