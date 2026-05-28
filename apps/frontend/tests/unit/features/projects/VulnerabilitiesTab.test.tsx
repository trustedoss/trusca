/**
 * VulnerabilitiesTab — unit tests (PR #11).
 *
 * Validates loading skeleton, empty state, error state, and that filter +
 * sort changes hit the wire layer with the right params at offset 0.
 *
 * We mock the wire layer so the component renders without a backend, and
 * stub `react-virtuoso` with a plain renderer so all rows mount in jsdom.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  VulnerabilityListItem,
  VulnerabilityListResponse,
} from "@/features/projects/api/vulnerabilitiesApi";
import { VulnerabilitiesTab } from "@/features/projects/components/VulnerabilitiesTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/vulnerabilitiesApi", async () => {
  return {
    listProjectVulnerabilities: vi.fn(),
    getVulnerabilityFinding: vi.fn(),
    updateVulnerabilityStatus: vi.fn(),
    bulkTransitionVulnerabilities: vi.fn(),
    extractAllowedTo: vi.fn(() => null),
    isConflictError: vi.fn(() => false),
    BULK_TRANSITION_MAX: 200,
  };
});

vi.mock("@/features/projects/api/vulnReportApi", async () => {
  return {
    fetchVulnerabilityReportPdf: vi.fn(),
  };
});

vi.mock("react-virtuoso", () => ({
  Virtuoso: <T,>({
    data,
    itemContent,
  }: {
    data: T[];
    itemContent: (index: number, item: T) => React.ReactNode;
  }) => (
    <div data-testid="virtuoso-stub">
      {data.map((item, idx) => (
        <div key={idx}>{itemContent(idx, item)}</div>
      ))}
    </div>
  ),
}));

import { fetchVulnerabilityReportPdf } from "@/features/projects/api/vulnReportApi";
import {
  bulkTransitionVulnerabilities,
  getVulnerabilityFinding,
  listProjectVulnerabilities,
} from "@/features/projects/api/vulnerabilitiesApi";

const mockedList = vi.mocked(listProjectVulnerabilities);
const mockedGet = vi.mocked(getVulnerabilityFinding);
const mockedReport = vi.mocked(fetchVulnerabilityReportPdf);
const mockedBulk = vi.mocked(bulkTransitionVulnerabilities);

function vuln(
  cveId: string,
  overrides: Partial<VulnerabilityListItem> = {},
): VulnerabilityListItem {
  return {
    id: overrides.id ?? `00000000-0000-0000-0000-${cveId.padEnd(12, "0").slice(0, 12)}`,
    cve_id: cveId,
    severity: "high",
    cvss_score: 7.5,
    epss_score: 0.42,
    epss_percentile: 0.7,
    summary: `summary for ${cveId}`,
    status: "new",
    analysis_source: null,
    reachable: null,
    reachability_source: null,
    reachability_analyzed_at: null,
    affected_component_count: 1,
    affected_component_name: null,
    affected_component_version: null,
    affected_component_license: null,
    affected_component_license_category: null,
    component_license_category: "unknown",
    discovered_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function listResponse(
  items: VulnerabilityListItem[],
  total = items.length,
  offset = 0,
  limit = 100,
): VulnerabilityListResponse {
  return { items, total, limit, offset };
}

function renderTab(
  initialEntries: string[] = ["/projects/proj-1"],
  projectName: string | null = "My Project",
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <VulnerabilitiesTab projectId="proj-1" projectName={projectName} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("VulnerabilitiesTab", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
    mockedReport.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders skeleton while loading", () => {
    mockedList.mockReturnValue(new Promise(() => {}));
    renderTab();
    expect(screen.getByTestId("vulnerabilities-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no findings match", async () => {
    mockedList.mockResolvedValueOnce(listResponse([]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerabilities-empty")).toBeInTheDocument();
    });
  });

  it("renders rows once data arrives and exposes summary counts", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111", { severity: "critical" }),
        vuln("CVE-2024-2222", { severity: "high" }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2);
    });
    const summary = screen.getByTestId("vulnerabilities-summary");
    expect(summary).toHaveAttribute("data-loaded", "2");
    expect(summary).toHaveAttribute("data-total", "2");
  });

  it("renders the RFC 7807 detail in an alert on error", async () => {
    mockedList.mockRejectedValueOnce(
      new ProblemError("not allowed", {
        status: 403,
        title: "Forbidden",
        detail: "Custom 7807 detail surfaces here.",
        problem: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerabilities-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("vulnerabilities-error").textContent).toContain(
      "Custom 7807 detail surfaces here.",
    );
  });

  it("debounces the search input then refetches with the new query", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    expect(mockedList).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ search: undefined }),
    );

    const search = screen.getByTestId("vulnerabilities-search");
    await userEvent.type(search, "CVE");
    expect(mockedList).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ search: "CVE" }),
      );
    });
  });

  // W4-B #19 — the severity / license MultiSelect drops are gone from the
  // toolbar. Severity now arrives via the Overview chart deep-link
  // (`?severity=critical`) and is surfaced as an ActiveFilterChips chip; the
  // user clears it from there.
  it("hydrating ?severity=critical surfaces a removable chip and forwards the wire filter", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?severity=critical"]);
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    expect(mockedList).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ severity: ["critical"], offset: 0 }),
    );
    const chip = await screen.findByTestId("active-filter-chip");
    expect(chip.getAttribute("data-facet")).toBe("severity");
    expect(chip.getAttribute("data-value")).toBe("critical");

    mockedList.mockClear();
    await userEvent.click(screen.getByTestId("active-filter-chip-clear"));
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ severity: undefined }),
      );
    });
  });

  it("changing the status filter triggers a fresh query at offset 0", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    // Open the MultiSelect dropdown, then toggle the "analyzing" checkbox row.
    await userEvent.click(screen.getByTestId("vulnerabilities-status-filter"));
    const analyzing = await waitFor(() => {
      const option = screen
        .getAllByTestId("vulnerabilities-status-filter-option")
        .find((el) => el.getAttribute("data-value") === "analyzing");
      if (!option) throw new Error("analyzing option not mounted");
      return option;
    });
    await userEvent.click(analyzing);

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ status: ["analyzing"], offset: 0 }),
      );
    });
  });

  // W4-B #19 — sort / order are now on the column headers (SortableColumnHeader
  // primitive). The toolbar no longer hosts <select> drops.
  it("clicking the CVSS column header triggers a query with sort=cvss", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    expect(screen.queryByTestId("vulnerabilities-sort")).not.toBeInTheDocument();

    mockedList.mockClear();
    await userEvent.click(
      screen.getByTestId("vulnerabilities-sort-header-cvss"),
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "cvss" }),
      );
    });
  });

  it("clicking a header cycles asc → desc on the second click", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });

    mockedList.mockClear();
    await userEvent.click(
      screen.getByTestId("vulnerabilities-sort-header-cvss"),
    );
    await waitFor(() => {
      expect(
        screen
          .getByTestId("vulnerabilities-sort-header-cvss")
          .getAttribute("data-sort-order"),
      ).toBe("asc");
    });
    mockedList.mockClear();
    await userEvent.click(
      screen.getByTestId("vulnerabilities-sort-header-cvss"),
    );
    await waitFor(() => {
      expect(
        screen
          .getByTestId("vulnerabilities-sort-header-cvss")
          .getAttribute("data-sort-order"),
      ).toBe("desc");
    });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "cvss", order: "desc" }),
      );
    });
  });

  it("hydrates filter state from the URL on first render (CSV)", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab([
      "/projects/proj-1?severity=critical,high&status=new,analyzing&sort=cvss&order=asc",
    ]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          severity: ["critical", "high"],
          status: ["new", "analyzing"],
          sort: "cvss",
          order: "asc",
        }),
      );
    });
  });

  it("clicking a row sets ?vuln=<finding_id> in the URL and opens the drawer", async () => {
    const item = vuln("CVE-2024-1111", {
      id: "00000000-0000-0000-0000-1111aaaa1111",
    });
    mockedList.mockResolvedValueOnce(listResponse([item]));
    mockedGet.mockReturnValue(new Promise(() => {})); // never resolves
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerability-row")).toBeInTheDocument();
    });
    // W2 #33b — the row container is now a <div>; the openable button is the
    // inner `vulnerability-row-open` element (split from the checkbox cell).
    await userEvent.click(screen.getByTestId("vulnerability-row-open"));
    await waitFor(() => {
      expect(screen.getByTestId("vulnerability-drawer")).toBeInTheDocument();
    });
  });

  // ─── v2.1 A3 — VEX provenance marker + "suppressed via VEX" filter ─────

  it("shows a VEX marker on a row whose status came from a VEX import", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111", { analysis_source: "vex_import" }),
        vuln("CVE-2024-2222", { analysis_source: "manual" }),
      ]),
    );
    renderTab();
    await waitFor(() =>
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2),
    );
    // Exactly one row carries the VEX marker.
    expect(
      screen.getAllByTestId("vulnerability-row-vex-marker"),
    ).toHaveLength(1);
  });

  it("the 'VEX-suppressed only' filter narrows the page to vex_import rows", async () => {
    const user = userEvent.setup();
    mockedList.mockResolvedValue(
      listResponse([
        vuln("CVE-2024-1111", {
          id: "00000000-0000-0000-0000-1111aaaa1111",
          analysis_source: "vex_import",
        }),
        vuln("CVE-2024-2222", {
          id: "00000000-0000-0000-0000-2222bbbb2222",
          analysis_source: "manual",
        }),
      ]),
    );
    renderTab();
    await waitFor(() =>
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2),
    );

    await user.click(
      screen.getByTestId("vulnerabilities-vex-suppressed-filter"),
    );

    await waitFor(() =>
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1),
    );
    expect(
      screen.getByTestId("vulnerability-row").getAttribute("data-cve-id"),
    ).toBe("CVE-2024-1111");
  });

  it("hydrates the VEX-suppressed filter from ?vex_suppressed=1", async () => {
    mockedList.mockResolvedValue(
      listResponse([
        vuln("CVE-2024-1111", { analysis_source: "vex_import" }),
        vuln("CVE-2024-2222", { analysis_source: "manual" }),
      ]),
    );
    renderTab(["/projects/proj-1?vex_suppressed=1"]);
    await waitFor(() =>
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1),
    );
    expect(
      (
        screen.getByTestId(
          "vulnerabilities-vex-suppressed-filter",
        ) as HTMLInputElement
      ).checked,
    ).toBe(true);
  });

  it("exposes the VEX export buttons + (gated) import trigger in the toolbar", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() =>
      expect(screen.getByTestId("vex-export-openvex")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("vex-export-cyclonedx")).toBeInTheDocument();
    // Default project role is developer (overview unresolved) → import gated.
    expect(screen.getByTestId("vex-import-open")).toBeDisabled();
  });

  // PDF Download moved to the Reports tab (user-test follow-up
  // 2026-05-27). The toolbar no longer hosts the button — the Reports
  // tab's vuln-pdf card does. The four previous Download-PDF tests are
  // replaced by this single negation; the download flow itself is now
  // covered by Reports-tab tests.
  it("no longer renders the Download PDF button in the toolbar", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerabilities-tab")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("vuln-download-pdf")).not.toBeInTheDocument();
  });

  // ----- EPSS first-class surface (v2.1) -----------------------------------

  it("renders the EPSS score as a one-decimal percentage in the row", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111", { epss_score: 0.973, epss_percentile: 0.91 }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerability-row-epss")).toBeInTheDocument();
    });
    const cell = screen.getByTestId("vulnerability-row-epss");
    expect(cell.textContent).toBe("97.3%");
    // Percentile is folded into the tooltip ("Top 9%"), not a second column.
    expect(cell.getAttribute("title")).toContain("Top 9%");
  });

  it("renders an em-dash for a finding with no EPSS score", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-2222", { epss_score: null, epss_percentile: null }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerability-row-epss")).toBeInTheDocument();
    });
    const cell = screen.getByTestId("vulnerability-row-epss");
    expect(cell.textContent).toBe("—");
    expect(cell).toHaveAttribute("data-epss-empty", "true");
  });

  it("clicking the EPSS column header requests sort=epss from the wire", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.click(
      screen.getByTestId("vulnerabilities-sort-header-epss"),
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "epss" }),
      );
    });
  });

  it("typing an EPSS threshold forwards min_epss at offset 0", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.type(
      screen.getByTestId("vulnerabilities-min-epss"),
      "0.5",
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ min_epss: 0.5, offset: 0 }),
      );
    });
  });

  it("clears the EPSS threshold and drops min_epss from the query", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?min_epss=0.5"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ min_epss: 0.5 }),
      );
    });
    mockedList.mockClear();

    await userEvent.click(
      screen.getByTestId("vulnerabilities-min-epss-clear"),
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ min_epss: undefined }),
      );
    });
  });

  it("hydrates the EPSS threshold from the URL on first render", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?min_epss=0.8&sort=epss&order=asc"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          min_epss: 0.8,
          sort: "epss",
          order: "asc",
        }),
      );
    });
  });

  // ----- Reachability surface (v2.3 r2) -----------------------------------

  it("renders a Reachable badge on a reachable row and omits the badge for not-analyzed", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111", {
          reachable: true,
          reachability_source: "govulncheck",
        }),
        vuln("CVE-2024-2222", { reachable: null }),
      ]),
    );
    renderTab();
    await waitFor(() =>
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2),
    );
    // The reachable row carries the loud badge; the not-analyzed row omits it
    // (compact mode renders nothing for null).
    expect(
      screen.getByTestId("reachability-badge-reachable"),
    ).toBeInTheDocument();
    expect(
      screen.queryByTestId("reachability-badge-unknown"),
    ).not.toBeInTheDocument();
  });

  it("renders a 'Not reachable' badge for a proven-unreachable row", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-3333", {
          reachable: false,
          reachability_source: "govulncheck",
        }),
      ]),
    );
    renderTab();
    await waitFor(() =>
      expect(
        screen.getByTestId("reachability-badge-unreachable"),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByTestId("reachability-badge-unreachable").textContent,
    ).toContain("Not reachable");
  });

  it("selecting the reachable filter forwards ?reachable=true at offset 0", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("vulnerabilities-reachable-filter"),
      "true",
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ reachable: "true", offset: 0 }),
      );
    });
  });

  it("resetting the reachable filter to 'any' drops reachable from the query", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?reachable=false"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ reachable: "false" }),
      );
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("vulnerabilities-reachable-filter"),
      "",
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ reachable: undefined }),
      );
    });
  });

  it("clicking the reachability column header requests sort=reachable from the wire", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.click(
      screen.getByTestId("vulnerabilities-sort-header-reachable"),
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "reachable" }),
      );
    });
  });

  it("hydrates the reachable filter from the URL on first render", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?reachable=unknown"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ reachable: "unknown" }),
      );
    });
    expect(
      (
        screen.getByTestId(
          "vulnerabilities-reachable-filter",
        ) as HTMLSelectElement
      ).value,
    ).toBe("unknown");
  });

  it("ignores an out-of-range reachable URL value (no wire param)", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?reachable=bogus"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ reachable: undefined }),
      );
    });
  });

  // ----- License risk axis (W2 #33) ---------------------------------------
  // User-test follow-up (2026-05-27): the row-level License column was
  // dropped — the drawer still carries SPDX + category, and the
  // `?license_category=` filter / chip flow continues to work. Single
  // negation test below; the License chip + filter tests further down keep
  // covering the URL-state flow.

  it("no longer renders a License column / cell on each row", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerabilities-header")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("vulnerabilities-header").textContent,
    ).not.toContain("License");
    expect(screen.queryByTestId("vuln-row-license")).not.toBeInTheDocument();
  });

  // -----------------------------------------------------------------
  // Follow-up to W4-B — Component@Version + License SPDX wired into the
  // list row from the BE schema bump (affected_component_{name,version,
  // license,license_category}).
  // -----------------------------------------------------------------

  it("renders Component@Version in the new Component column", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111", {
          affected_component_name: "lodash",
          affected_component_version: "4.17.20",
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(
        screen.getByTestId("vulnerability-row-component"),
      ).toBeInTheDocument();
    });
    const cell = screen.getByTestId("vulnerability-row-component");
    expect(cell.textContent).toContain("lodash@4.17.20");
    expect(cell.getAttribute("data-component-name")).toBe("lodash");
    expect(cell.getAttribute("data-component-version")).toBe("4.17.20");
    // count == 1 → no "+N-1" suffix badge.
    expect(
      screen.queryByTestId("vulnerability-row-component-more"),
    ).not.toBeInTheDocument();
  });

  it("appends a +N-1 suffix when the CVE touches additional cvs", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-2222", {
          affected_component_name: "axios",
          affected_component_version: "0.21.1",
          affected_component_count: 3,
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(
        screen.getByTestId("vulnerability-row-component-more"),
      ).toBeInTheDocument();
    });
    const cell = screen.getByTestId("vulnerability-row-component");
    expect(cell.getAttribute("data-affected-count")).toBe("3");
    expect(
      screen.getByTestId("vulnerability-row-component-more").textContent,
    ).toBe("+2");
  });

  it("renders the dash placeholder when the pinned cv is unknown", async () => {
    // Legacy / CASCADE-deleted rows can leave name+version null. The cell
    // must render the localized em-dash, never the bare string "null@null".
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-3333", {
          affected_component_name: null,
          affected_component_version: null,
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(
        screen.getByTestId("vulnerability-row-component"),
      ).toBeInTheDocument();
    });
    const cell = screen.getByTestId("vulnerability-row-component");
    expect(cell.textContent).not.toContain("null");
    expect(cell.getAttribute("data-component-name")).toBe("");
    expect(cell.getAttribute("data-component-version")).toBe("");
  });

  // (License-cell tests removed — the column was dropped from the row in the
  // user-test follow-up. The drawer still surfaces SPDX + category; the
  // `?license_category=` filter + ActiveFilterChips flow is covered below.)

  // W4-B #19 — license MultiSelect dropped from toolbar; arrives via the
  // Overview chart deep-link and is surfaced via ActiveFilterChips.
  it("hydrating ?license_category=forbidden surfaces a removable chip", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?license_category=forbidden"]);
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    expect(mockedList).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({
        license_category: ["forbidden"],
        offset: 0,
      }),
    );
    const chip = await screen.findByTestId("active-filter-chip");
    expect(chip.getAttribute("data-facet")).toBe("license_category");
    expect(chip.getAttribute("data-value")).toBe("forbidden");
  });

  it("hydrates the license filter from ?license_category=forbidden,allowed", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab(["/projects/proj-1?license_category=forbidden,allowed"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          license_category: ["forbidden", "allowed"],
        }),
      );
    });
  });

  // ─── W2 #33b — bulk-transition selection + action bar ─────────────────

  it("does not render the bulk action bar until a row is selected", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    expect(
      screen.queryByTestId("vulnerabilities-bulk-action-bar"),
    ).not.toBeInTheDocument();
  });

  it("toggling a row checkbox surfaces the action bar with the selected count", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111"),
        vuln("CVE-2024-2222"),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2);
    });
    const checkboxes = screen.getAllByTestId("vulnerability-row-checkbox");
    await userEvent.click(checkboxes[0]);
    expect(
      screen.getByTestId("vulnerabilities-bulk-action-bar"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("vulnerabilities-bulk-selected-count").textContent,
    ).toContain("1");
    await userEvent.click(checkboxes[1]);
    expect(
      screen.getByTestId("vulnerabilities-bulk-selected-count").textContent,
    ).toContain("2");
  });

  it("checking the header select-all selects every row on the current page", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111"),
        vuln("CVE-2024-2222"),
        vuln("CVE-2024-3333"),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(3);
    });
    await userEvent.click(screen.getByTestId("vulnerabilities-select-all"));
    expect(
      screen.getByTestId("vulnerabilities-bulk-selected-count").textContent,
    ).toContain("3");
    // Re-clicking the header select-all clears the selection.
    await userEvent.click(screen.getByTestId("vulnerabilities-select-all"));
    expect(
      screen.queryByTestId("vulnerabilities-bulk-action-bar"),
    ).not.toBeInTheDocument();
  });

  it("apply posts the selected ids + target status and shows succeeded/failed counts", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111", { id: "row-aaaa" }),
        vuln("CVE-2024-2222", { id: "row-bbbb" }),
      ]),
    );
    mockedBulk.mockResolvedValueOnce({
      target_status: "analyzing",
      total: 2,
      succeeded: 1,
      failed: 1,
      results: [
        {
          finding_id: "row-aaaa",
          success: true,
          status_code: 200,
          error: null,
          detail: null,
          allowed_to: null,
        },
        {
          finding_id: "row-bbbb",
          success: false,
          status_code: 422,
          error: "invalid_transition",
          detail: "finding is already in status 'analyzing'",
          allowed_to: null,
        },
      ],
    });
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2);
    });
    await userEvent.click(screen.getByTestId("vulnerabilities-select-all"));
    // Choose the target status from the inline dropdown.
    await userEvent.selectOptions(
      screen.getByTestId("vulnerabilities-bulk-target-select"),
      "analyzing",
    );
    await userEvent.click(screen.getByTestId("vulnerabilities-bulk-apply"));
    await waitFor(() => {
      expect(mockedBulk).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          finding_ids: ["row-aaaa", "row-bbbb"],
          target_status: "analyzing",
        }),
      );
    });
    // Result alert reports the per-row outcome counts.
    await waitFor(() => {
      expect(screen.getByTestId("vulnerabilities-bulk-result")).toHaveAttribute(
        "data-succeeded",
        "1",
      );
    });
    expect(screen.getByTestId("vulnerabilities-bulk-result")).toHaveAttribute(
      "data-failed",
      "1",
    );
    // Per-row failure preview surfaces the detail.
    const failure = screen.getByTestId(
      "vulnerabilities-bulk-result-failure",
    );
    expect(failure.textContent).toContain("already in status");
  });

  // (License-column row test removed — the column was dropped from the row
  // in the user-test follow-up.)

  it("does not render an ActiveFilterChips row when neither facet is active", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    expect(
      screen.queryByTestId("active-filter-chips"),
    ).not.toBeInTheDocument();
  });

  it("changing a filter clears the selection", async () => {
    mockedList.mockResolvedValue(
      listResponse([
        vuln("CVE-2024-1111"),
        vuln("CVE-2024-2222"),
      ]),
    );
    renderTab(["/projects/proj-1?severity=high"]);
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2);
    });
    await userEvent.click(screen.getByTestId("vulnerabilities-select-all"));
    expect(
      screen.getByTestId("vulnerabilities-bulk-action-bar"),
    ).toBeInTheDocument();

    // W4-B #19 — removing a severity filter via the chips row mutates the
    // severity state which (per the filter-change effect) drops the selection.
    await userEvent.click(screen.getByTestId("active-filter-chip-clear"));
    await waitFor(() => {
      expect(
        screen.queryByTestId("vulnerabilities-bulk-action-bar"),
      ).not.toBeInTheDocument();
    });
  });

  // -------------------------------------------------------------------------
  // W9 #52 — "+ Add filter" dropdown + ColumnsPicker integration. The dropdown
  // mounts an inline MultiSelect for facets that were previously chip-only;
  // the columns picker toggles per-column visibility on the table.
  // -------------------------------------------------------------------------

  it("opening the +Add filter dropdown mounts the severity inline facet", async () => {
    window.localStorage.clear();
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-9999")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerability-row")).toBeInTheDocument();
    });
    // Facet is not visible by default — severity has no URL value.
    expect(
      screen.queryByTestId("vulnerabilities-severity-facet"),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByTestId("vulnerabilities-more-filters-trigger"),
    );
    const severityOption = await waitFor(() =>
      screen.getByTestId(
        "vulnerabilities-more-filters-trigger-option-severity",
      ),
    );
    await userEvent.click(severityOption);

    await waitFor(() => {
      expect(
        screen.getByTestId("vulnerabilities-severity-facet"),
      ).toBeInTheDocument();
    });
    // The MultiSelect itself is present so the user can pick severities.
    expect(
      screen.getByTestId("vulnerabilities-severity-filter"),
    ).toBeInTheDocument();
  });

  it("hiding the EPSS column via the ColumnsPicker drops the EPSS cell", async () => {
    window.localStorage.clear();
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-8888")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerability-row")).toBeInTheDocument();
    });
    // EPSS cell is rendered by default.
    expect(
      screen.getByTestId("vulnerability-row-epss"),
    ).toBeInTheDocument();

    await userEvent.click(
      screen.getByTestId("vulnerabilities-columns-picker-trigger"),
    );
    const epssOption = await waitFor(() =>
      screen.getByTestId("vulnerabilities-columns-picker-option-epss"),
    );
    await userEvent.click(epssOption);

    await waitFor(() => {
      expect(
        screen.queryByTestId("vulnerability-row-epss"),
      ).not.toBeInTheDocument();
    });
    // The required CVE-id cell is still present (header carries cve_id).
    expect(screen.getByTestId("vulnerability-row")).toBeInTheDocument();
  });
});
