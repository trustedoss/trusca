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
    extractAllowedTo: vi.fn(() => null),
    isConflictError: vi.fn(() => false),
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
  getVulnerabilityFinding,
  listProjectVulnerabilities,
} from "@/features/projects/api/vulnerabilitiesApi";

const mockedList = vi.mocked(listProjectVulnerabilities);
const mockedGet = vi.mocked(getVulnerabilityFinding);
const mockedReport = vi.mocked(fetchVulnerabilityReportPdf);

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
    affected_component_count: 1,
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

    const select = screen.getByTestId(
      "vulnerabilities-severity-filter",
    ) as HTMLSelectElement;
    Array.from(select.options).forEach((opt) => {
      opt.selected = opt.value === "critical";
    });
    select.dispatchEvent(new Event("change", { bubbles: true }));

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

    const select = screen.getByTestId(
      "vulnerabilities-status-filter",
    ) as HTMLSelectElement;
    Array.from(select.options).forEach((opt) => {
      opt.selected = opt.value === "analyzing";
    });
    select.dispatchEvent(new Event("change", { bubbles: true }));

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
    await userEvent.click(screen.getByTestId("vulnerability-row"));
    await waitFor(() => {
      expect(screen.getByTestId("vulnerability-drawer")).toBeInTheDocument();
    });
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
});
