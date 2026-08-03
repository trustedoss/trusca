/**
 * useScans + listMyScans + downloadSbom — api/glue tests.
 *
 * Pins:
 *   - `listMyScans` forwards every parameter to `GET /v1/scans`.
 *   - `scansQueryKey` shape (so a future refactor can't silently break the
 *     cache-invalidation paths).
 *   - `downloadSbom` parses the Content-Disposition filename and falls back
 *     gracefully when the header is missing.
 *   - `unarchiveProject` issues `PATCH { archived: false }` to the project URL.
 */
import { describe, expect, it, vi } from "vitest";

import { scansQueryKey } from "@/features/scans/useScans";
import { api } from "@/lib/api";
import {
  downloadSbom,
  listMyScans,
  unarchiveProject,
} from "@/lib/projectsApi";

describe("listMyScans", () => {
  it("forwards status, page, and size", async () => {
    const spy = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce({
        data: { items: [], total: 0, page: 1, size: 20 },
      } as never);
    await listMyScans({ status: "running", page: 2, size: 50 });
    expect(spy).toHaveBeenCalledWith("/v1/scans", {
      params: { status: "running", page: 2, size: 50 },
    });
    spy.mockRestore();
  });
});

describe("scansQueryKey", () => {
  it("normalizes defaults so two equivalent param shapes hit the same cache slot", () => {
    expect(scansQueryKey({})).toEqual([
      "scans",
      "list",
      { status: null, page: 1, size: 20 },
    ]);
    expect(scansQueryKey({ page: 1, size: 20 })).toEqual([
      "scans",
      "list",
      { status: null, page: 1, size: 20 },
    ]);
    expect(scansQueryKey({ status: "failed" })).toEqual([
      "scans",
      "list",
      { status: "failed", page: 1, size: 20 },
    ]);
  });
});

describe("downloadSbom", () => {
  it("parses the Content-Disposition filename when present", async () => {
    const spy = vi.spyOn(api, "get").mockResolvedValueOnce({
      data: new Blob(["{}"], { type: "application/json" }),
      headers: {
        "content-disposition": 'attachment; filename="sbom-demo.cdx.json"',
      },
    } as never);
    const out = await downloadSbom("proj-1", "cyclonedx-json");
    expect(out.filename).toBe("sbom-demo.cdx.json");
    expect(out.format).toBe("cyclonedx-json");
    expect(out.blob).toBeInstanceOf(Blob);
    spy.mockRestore();
  });

  it("falls back to a deterministic name when the header is missing", async () => {
    const spy = vi.spyOn(api, "get").mockResolvedValueOnce({
      data: new Blob(["x"], { type: "text/plain" }),
      headers: {},
    } as never);
    const out = await downloadSbom("proj-1", "spdx-tv");
    expect(out.filename).toBe("sbom-proj-1.spdx");
    spy.mockRestore();
  });

  it("requests the SBOM with the format query parameter", async () => {
    const spy = vi.spyOn(api, "get").mockResolvedValueOnce({
      data: new Blob(["x"]),
      headers: {},
    } as never);
    await downloadSbom("proj-1", "spdx-json");
    expect(spy).toHaveBeenCalledWith(
      "/v1/projects/proj-1/sbom",
      expect.objectContaining({
        params: { format: "spdx-json" },
        responseType: "blob",
      }),
    );
    spy.mockRestore();
  });
});

describe("unarchiveProject", () => {
  it("issues PATCH /v1/projects/{id} with archived: false", async () => {
    const spy = vi.spyOn(api, "patch").mockResolvedValueOnce({
      data: {
        id: "proj-1",
        team_id: "t1",
        name: "n",
        slug: "n",
        description: null,
        git_url: null,
        default_branch: null,
        visibility: "team",
        archived_at: null,
        created_by_user_id: null,
        latest_scan_id: null,
        created_at: "2026-05-08T00:00:00Z",
        updated_at: "2026-05-08T00:00:00Z",
      },
    } as never);
    await unarchiveProject("proj-1");
    expect(spy).toHaveBeenCalledWith("/v1/projects/proj-1", {
      archived: false,
    });
    spy.mockRestore();
  });
});
