/**
 * Smoke tests for the admin Health api + query-key shape.
 */
import { describe, expect, it, vi } from "vitest";

import { getAdminHealth } from "@/features/admin/health/api/adminHealthApi";
import { adminHealthQueryKey } from "@/features/admin/health/api/useAdminHealth";
import { api } from "@/lib/api";

describe("admin Health api glue", () => {
  it("getAdminHealth issues GET /v1/admin/health", async () => {
    const spy = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({
        data: { components: [], updated_at: "2026-05-08T00:00:00Z" },
      } as never);
    await getAdminHealth();
    expect(spy).toHaveBeenCalledWith("/v1/admin/health");
    spy.mockRestore();
  });

  it("query key is constant", () => {
    expect(adminHealthQueryKey()).toEqual(["admin", "health"]);
  });
});
