/**
 * Smoke tests for the admin Audit api + query-key shape.
 *
 * The CSV download path is exercised end-to-end through the page test;
 * here we pin the URL + filename parsing only.
 */
import { describe, expect, it, vi } from "vitest";

import {
  downloadAdminAuditCsv,
  searchAdminAudit,
} from "@/features/admin/audit/api/adminAuditApi";
import { adminAuditQueryKey } from "@/features/admin/audit/api/useAdminAudit";
import { api } from "@/lib/api";

describe("admin Audit api glue", () => {
  it("searchAdminAudit forwards every filter", async () => {
    const spy = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({
        data: { items: [], total: 0, page: 1, page_size: 50, has_more: false },
      } as never);
    await searchAdminAudit({
      actor_user_id: "actor-1",
      target_table: "scans",
      action: "create",
      from: "2026-05-01T00:00:00Z",
      to: "2026-05-08T00:00:00Z",
      q: "alpha",
      page: 2,
      page_size: 100,
    });
    expect(spy).toHaveBeenCalledWith("/v1/admin/audit", {
      params: {
        actor_user_id: "actor-1",
        target_table: "scans",
        action: "create",
        from: "2026-05-01T00:00:00Z",
        to: "2026-05-08T00:00:00Z",
        q: "alpha",
        page: 2,
        page_size: 100,
      },
    });
    spy.mockRestore();
  });

  it("downloadAdminAuditCsv parses the Content-Disposition filename", async () => {
    // jsdom exposes URL but not createObjectURL — patch only for this test.
    const original = (URL as unknown as Record<string, unknown>)
      .createObjectURL;
    (URL as unknown as Record<string, unknown>).createObjectURL = vi
      .fn()
      .mockReturnValue("blob:fake-url");
    try {
      const spy = vi
        .spyOn(api, "get")
        .mockResolvedValueOnce({
          data: new Blob(["a,b,c"], { type: "text/csv" }),
          headers: {
            "content-disposition":
              'attachment; filename="audit_export_20260501_20260508.csv"',
          },
        } as never);
      const out = await downloadAdminAuditCsv({});
      expect(out.filename).toBe("audit_export_20260501_20260508.csv");
      expect(out.blobUrl).toBe("blob:fake-url");
      spy.mockRestore();
    } finally {
      if (original === undefined) {
        delete (URL as unknown as Record<string, unknown>).createObjectURL;
      } else {
        (URL as unknown as Record<string, unknown>).createObjectURL = original;
      }
    }
  });

  it("downloadAdminAuditCsv falls back to audit_export.csv when the header is missing", async () => {
    const original = (URL as unknown as Record<string, unknown>)
      .createObjectURL;
    (URL as unknown as Record<string, unknown>).createObjectURL = vi
      .fn()
      .mockReturnValue("blob:fake-url");
    try {
      const spy = vi
        .spyOn(api, "get")
        .mockResolvedValueOnce({
          data: new Blob(["a,b,c"], { type: "text/csv" }),
          headers: {},
        } as never);
      const out = await downloadAdminAuditCsv({});
      expect(out.filename).toBe("audit_export.csv");
      spy.mockRestore();
    } finally {
      if (original === undefined) {
        delete (URL as unknown as Record<string, unknown>).createObjectURL;
      } else {
        (URL as unknown as Record<string, unknown>).createObjectURL = original;
      }
    }
  });

  it("query key fixes default page+size", () => {
    expect(adminAuditQueryKey({})).toEqual([
      "admin",
      "audit",
      {
        actor_user_id: null,
        target_table: null,
        action: null,
        from: null,
        to: null,
        q: null,
        page: 1,
        page_size: 50,
      },
    ]);
  });
});
