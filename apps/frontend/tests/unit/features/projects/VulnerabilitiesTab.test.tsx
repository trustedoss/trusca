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

  it("changing the severity filter triggers a fresh query at offset 0", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    // Open the MultiSelect dropdown, then toggle the "critical" checkbox row.
    await userEvent.click(screen.getByTestId("vulnerabilities-severity-filter"));
    const critical = await waitFor(() => {
      const option = screen
        .getAllByTestId("vulnerabilities-severity-filter-option")
        .find((el) => el.getAttribute("data-value") === "critical");
      if (!option) throw new Error("critical option not mounted");
      return option;
    });
    await userEvent.click(critical);

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ severity: ["critical"], offset: 0 }),
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

  it("changing the sort key triggers a query with that sort", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("vulnerabilities-sort"),
      "cvss",
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "cvss" }),
      );
    });
  });

  it("changing the order triggers a query with that order", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("vulnerabilities-order"),
      "asc",
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ order: "asc" }),
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

  it("renders the Download PDF report button in the toolbar", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vuln-download-pdf")).toBeInTheDocument();
    });
    expect(screen.getByTestId("vuln-download-pdf")).toHaveTextContent(
      "Download PDF report",
    );
  });

  it("clicking Download PDF fetches the report with the project id + name", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    mockedReport.mockResolvedValueOnce({
      blob: new Blob(["%PDF-1.7"], { type: "application/pdf" }),
      filename: "vulnerability-report-My-Project.pdf",
    });
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vuln-download-pdf")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("vuln-download-pdf"));

    await waitFor(() => {
      expect(mockedReport).toHaveBeenCalledWith("proj-1", "My Project");
    });
  });

  it("shows the generating label while the PDF is being fetched", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    // Never-resolving fetch keeps the button in its loading state.
    mockedReport.mockReturnValue(new Promise(() => {}));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vuln-download-pdf")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("vuln-download-pdf"));

    await waitFor(() => {
      const button = screen.getByTestId("vuln-download-pdf");
      expect(button).toHaveTextContent("Generating…");
      expect(button).toBeDisabled();
    });
  });

  it("surfaces an inline error when the PDF download fails", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    mockedReport.mockRejectedValueOnce(
      new ProblemError("Project not found.", {
        status: 404,
        title: "Not Found",
        detail: "Project not found.",
        problem: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vuln-download-pdf")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("vuln-download-pdf"));

    await waitFor(() => {
      expect(
        screen.getByTestId("vuln-download-pdf-error"),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("vuln-download-pdf-error").textContent,
    ).toContain("Project not found.");
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

  it("selecting the EPSS sort key requests sort=epss from the wire", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("vulnerabilities-sort"),
      "epss",
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

  it("selecting the reachability sort key requests sort=reachable from the wire", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("vulnerabilities-sort"),
      "reachable",
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

  it("renders the License column header", async () => {
    mockedList.mockResolvedValueOnce(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vulnerabilities-header")).toBeInTheDocument();
    });
    // The header carries the localized "License" label as plain text.
    expect(
      screen.getByTestId("vulnerabilities-header").textContent,
    ).toContain("License");
  });

  it("renders a LicenseCategoryBadge per row for each category", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        vuln("CVE-2024-1111", { component_license_category: "forbidden" }),
        vuln("CVE-2024-2222", { component_license_category: "conditional" }),
        vuln("CVE-2024-3333", { component_license_category: "allowed" }),
        vuln("CVE-2024-4444", { component_license_category: "unknown" }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vuln-row-license")).toHaveLength(4);
    });
    const cells = screen.getAllByTestId("vuln-row-license");
    expect(cells[0].getAttribute("data-license-category")).toBe("forbidden");
    expect(cells[1].getAttribute("data-license-category")).toBe("conditional");
    expect(cells[2].getAttribute("data-license-category")).toBe("allowed");
    expect(cells[3].getAttribute("data-license-category")).toBe("unknown");
    // The shared LicenseCategoryBadge surfaces a stable per-category testid.
    expect(
      screen.getByTestId("license-category-badge-forbidden"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("license-category-badge-conditional"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("license-category-badge-allowed"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("license-category-badge-unknown"),
    ).toBeInTheDocument();
  });

  it("defensively renders the 'unknown' bucket when the wire field is missing", async () => {
    // Backend contract guarantees the field, but if a stale-cached row drops
    // it we still render the unknown badge instead of crashing.
    const bare = vuln("CVE-2024-9999");
    // Strip the field as if it never landed (the type guarantees it, so we
    // unsafely cast to simulate a contract drift / regression).
    delete (bare as Partial<VulnerabilityListItem>).component_license_category;
    mockedList.mockResolvedValueOnce(listResponse([bare]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("vuln-row-license")).toBeInTheDocument();
    });
    expect(
      screen.getByTestId("license-category-badge-unknown"),
    ).toBeInTheDocument();
  });

  it("selecting the license filter forwards ?license_category=forbidden at offset 0", async () => {
    mockedList.mockResolvedValue(listResponse([vuln("CVE-2024-1111")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    // Open the MultiSelect dropdown and toggle the "forbidden" row.
    await userEvent.click(screen.getByTestId("vulnerabilities-license-filter"));
    const forbidden = await waitFor(() => {
      const option = screen
        .getAllByTestId("vulnerabilities-license-filter-option")
        .find((el) => el.getAttribute("data-value") === "forbidden");
      if (!option) throw new Error("forbidden option not mounted");
      return option;
    });
    await userEvent.click(forbidden);

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          license_category: ["forbidden"],
          offset: 0,
        }),
      );
    });
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

  it("changing a filter clears the selection", async () => {
    mockedList.mockResolvedValue(
      listResponse([
        vuln("CVE-2024-1111"),
        vuln("CVE-2024-2222"),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("vulnerability-row")).toHaveLength(2);
    });
    await userEvent.click(screen.getByTestId("vulnerabilities-select-all"));
    expect(
      screen.getByTestId("vulnerabilities-bulk-action-bar"),
    ).toBeInTheDocument();

    // Toggle a severity filter via the toolbar — should drop the selection.
    await userEvent.click(screen.getByTestId("vulnerabilities-severity-filter"));
    const critical = await waitFor(() => {
      const option = screen
        .getAllByTestId("vulnerabilities-severity-filter-option")
        .find((el) => el.getAttribute("data-value") === "critical");
      if (!option) throw new Error("critical option not mounted");
      return option;
    });
    await userEvent.click(critical);
    await waitFor(() => {
      expect(
        screen.queryByTestId("vulnerabilities-bulk-action-bar"),
      ).not.toBeInTheDocument();
    });
  });
});
