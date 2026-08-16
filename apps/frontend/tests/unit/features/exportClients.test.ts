/**
 * The three export clients send what the three lists send (B5).
 *
 * The claim this feature rests on is that a filtered screen and its exported
 * file cannot disagree. On the backend that holds because the export pages
 * the list service. On the client it holds because both run the same params
 * object through the same query builder, and that is what these pin: the
 * request the export issues, parameter for parameter, against the request
 * the list issues from the same input.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import * as csvExport from "@/lib/csvExport";
import {
  exportProjectVulnerabilitiesCsv,
  listProjectVulnerabilities,
} from "@/features/projects/api/vulnerabilitiesApi";
import {
  exportProjectComponentsCsv,
  listProjectComponents,
} from "@/features/projects/api/projectDetailApi";
import {
  exportInventoryComponentsCsv,
  listInventoryComponents,
} from "@/features/inventory/api/inventoryApi";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);

/** The params the export actually put on the wire. */
function capturedExportParams(): Record<string, unknown> {
  const spy = vi.mocked(csvExport.downloadCsvExport);
  return spy.mock.calls[0]?.[1] as Record<string, unknown>;
}

vi.mock("@/lib/csvExport", async (importOriginal) => {
  const actual = await importOriginal<typeof csvExport>();
  return { ...actual, downloadCsvExport: vi.fn(async () => {}) };
});

beforeEach(() => {
  mockedGet.mockReset();
  mockedGet.mockResolvedValue({ data: { items: [], total: 0 } } as never);
  vi.mocked(csvExport.downloadCsvExport).mockClear();
});

describe("vulnerabilities export", () => {
  it("sends the same filters the list sends", async () => {
    const filters = {
      search: "log4j",
      severity: ["critical" as const, "high" as const],
      status: ["analyzing" as const],
      sort: "priority" as const,
      order: "desc" as const,
      min_epss: 0.5,
      reachable: "true" as const,
      sla: "overdue" as const,
      license_category: ["forbidden" as const],
      scanId: "scan-1",
    };

    await listProjectVulnerabilities("p1", { ...filters, limit: 50 });
    const listParams = mockedGet.mock.calls[0]?.[1]?.params as Record<
      string,
      unknown
    >;

    await exportProjectVulnerabilitiesCsv("p1", { ...filters, limit: 50 });
    const exportParams = capturedExportParams();

    // Everything except the page window, which an export has no use for.
    const { limit: _l, offset: _o, ...listFilters } = listParams;
    expect(exportParams).toEqual(listFilters);
  });

  it("drops the page window rather than exporting one page", async () => {
    await exportProjectVulnerabilitiesCsv("p1", { limit: 50, offset: 100 });
    expect(capturedExportParams()).not.toHaveProperty("limit");
    expect(capturedExportParams()).not.toHaveProperty("offset");
  });

  it("targets the project's own export path", async () => {
    await exportProjectVulnerabilitiesCsv("p1", {});
    expect(vi.mocked(csvExport.downloadCsvExport).mock.calls[0]?.[0]).toBe(
      "/v1/projects/p1/vulnerabilities/export.csv",
    );
  });
});

describe("components export", () => {
  it("sends the same filters the list sends", async () => {
    const filters = {
      search: "openssl",
      severity: ["critical" as const],
      license_category: ["forbidden" as const],
      direct: true,
      dependency_scope: ["required" as const],
      eol: true,
      outdated: true,
      malicious: true,
      sort: "severity" as const,
      order: "desc" as const,
    };

    await listProjectComponents("p1", { ...filters, limit: 50 });
    const listParams = mockedGet.mock.calls[0]?.[1]?.params as Record<
      string,
      unknown
    >;

    await exportProjectComponentsCsv("p1", { ...filters, limit: 50 });

    const { limit: _l, offset: _o, ...listFilters } = listParams;
    expect(capturedExportParams()).toEqual(listFilters);
  });
});

describe("inventory export", () => {
  it("sends the same filters the list sends", async () => {
    const filters = {
      q: "lodash",
      packageType: ["npm"],
      severity: ["critical" as const],
      licenseCategory: ["forbidden" as const],
      eol: true,
      outdated: false,
      sort: "name" as const,
      order: "asc" as const,
    };

    await listInventoryComponents({ ...filters, limit: 50 });
    const listParams = mockedGet.mock.calls[0]?.[1]?.params as Record<
      string,
      unknown
    >;

    await exportInventoryComponentsCsv({ ...filters, limit: 50 });

    const { limit: _l, offset: _o, ...listFilters } = listParams;
    expect(capturedExportParams()).toEqual(listFilters);
  });
});
