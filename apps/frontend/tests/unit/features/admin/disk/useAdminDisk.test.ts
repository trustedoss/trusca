/**
 * Smoke tests for the admin Disk api + query-key shape.
 */
import { describe, expect, it, vi } from "vitest";

import { getAdminDisk } from "@/features/admin/disk/api/adminDiskApi";
import { adminDiskQueryKey } from "@/features/admin/disk/api/useAdminDisk";
import { api } from "@/lib/api";

describe("admin Disk api glue", () => {
  it("getAdminDisk issues GET /v1/admin/disk", async () => {
    const spy = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({
        data: { items: [], collected_at: "2026-05-08T00:00:00Z" },
      } as never);
    await getAdminDisk();
    expect(spy).toHaveBeenCalledWith("/v1/admin/disk");
    spy.mockRestore();
  });

  it("query key is constant", () => {
    expect(adminDiskQueryKey()).toEqual(["admin", "disk"]);
  });
});
