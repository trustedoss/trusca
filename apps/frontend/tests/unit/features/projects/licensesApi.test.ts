/**
 * licensesApi — wire layer tests (Phase D review-flag facet).
 *
 * Direct unit tests for the axios wrapper's query serialization, ensuring the
 * client `reviewFlag` maps to the backend's singular `review_flag` param and is
 * omitted when unset. Mirrors the projectDetailApi wire-test convention.
 */
import type { AxiosInstance } from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => {
  const get = vi.fn();
  return { api: { get } as unknown as AxiosInstance };
});

import { api } from "@/lib/api";
import {
  listProjectLicenses,
  REVIEW_FLAG_VALUES,
} from "@/features/projects/api/licensesApi";

const mockedGet = api.get as unknown as ReturnType<typeof vi.fn>;

describe("licensesApi.listProjectLicenses", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockResolvedValue({
      data: { items: [], distribution: {}, total: 0 },
    });
  });

  it("hits /v1/projects/{id}/licenses", async () => {
    await listProjectLicenses("proj-1");
    expect(mockedGet).toHaveBeenCalledWith(
      "/v1/projects/proj-1/licenses",
      expect.objectContaining({ params: {} }),
    );
  });

  it.each(REVIEW_FLAG_VALUES)(
    "serializes reviewFlag %s to the singular review_flag param",
    async (flag) => {
      await listProjectLicenses("proj-1", { reviewFlag: flag });
      const call = mockedGet.mock.calls[0]!;
      expect(call[1].params).toMatchObject({ review_flag: flag });
    },
  );

  it("omits review_flag when no reviewFlag is set", async () => {
    await listProjectLicenses("proj-1", { search: "mit" });
    const call = mockedGet.mock.calls[0]!;
    expect(call[1].params).not.toHaveProperty("review_flag");
    expect(call[1].params).toMatchObject({ search: "mit" });
  });
});
