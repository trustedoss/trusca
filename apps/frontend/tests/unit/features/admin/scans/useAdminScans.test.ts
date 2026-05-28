/**
 * Smoke tests for the admin Scans api + query-key shape.
 */
import { describe, expect, it, vi } from "vitest";

import {
  cancelAdminScan,
  listAdminScans,
} from "@/features/admin/scans/api/adminScansApi";
import { adminScansQueryKey } from "@/features/admin/scans/api/useAdminScans";
import { api } from "@/lib/api";

describe("admin Scans api glue", () => {
  it("listAdminScans forwards filter params", async () => {
    const spy = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({
        data: { items: [], total: 0, page: 1, page_size: 50 },
      } as never);
    await listAdminScans({ page: 2, page_size: 25, status: "running" });
    expect(spy).toHaveBeenCalledWith("/v1/admin/scans", {
      params: { page: 2, page_size: 25, status: "running" },
    });
    spy.mockRestore();
  });

  it("cancelAdminScan POSTs to /v1/admin/scans/{id}/cancel", async () => {
    const spy = vi
      .spyOn(api, "post")
      .mockResolvedValueOnce({ data: { id: "abc" } } as never);
    await cancelAdminScan("scan-1");
    expect(spy).toHaveBeenCalledWith("/v1/admin/scans/scan-1/cancel");
    spy.mockRestore();
  });

  it("query keys carry the filter shape", () => {
    expect(adminScansQueryKey({})).toEqual([
      "admin",
      "scans",
      { page: 1, page_size: 50, status: null },
    ]);
    expect(adminScansQueryKey({ status: "queued" })).toEqual([
      "admin",
      "scans",
      { page: 1, page_size: 50, status: "queued" },
    ]);
  });
});
