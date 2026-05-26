/**
 * ReportsTab — unit tests (W3 #32).
 *
 * Covers:
 *   1) happy path — rows render, page-of-N label, all 4 generate cards mounted.
 *   2) empty state.
 *   3) loading state (skeleton) + error state (Alert).
 *   4) Type filter toggle → query refetch with new params + `?rpt_type=…` set.
 *   5) Card deeplink → `?tab=…` set without dropping `?scan=`.
 *   6) Pagination next → `?rpt_page=2` + refetch.
 *
 * Mocks the wire fetcher directly so we don't need axios / a network stack.
 * The MultiSelect dropdown uses Radix DropdownMenu which the global setup
 * polyfills (pointer-capture stubs in tests/setup.ts).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ReportDownloadEntry,
  ReportHistoryResponse,
} from "@/features/projects/api/reportHistoryApi";
import { ReportsTab } from "@/features/projects/components/ReportsTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/reportHistoryApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/reportHistoryApi")
  >("@/features/projects/api/reportHistoryApi");
  return {
    ...actual,
    fetchReportHistory: vi.fn(),
  };
});

import { fetchReportHistory } from "@/features/projects/api/reportHistoryApi";

const mockedFetch = vi.mocked(fetchReportHistory);

function row(
  overrides: Partial<ReportDownloadEntry> = {},
): ReportDownloadEntry {
  // Use `in` checks so explicit nulls survive (rows with deleted users / pruned
  // scans / unknown size are valid fixtures that exercise the row's fallback
  // branches).
  return {
    id: overrides.id ?? "row-1",
    project_id: overrides.project_id ?? "proj-1",
    scan_id:
      "scan_id" in overrides
        ? (overrides.scan_id as string | null)
        : "scan-abcdef12-0000-0000-0000-000000000000",
    team_id: overrides.team_id ?? "team-1",
    user:
      "user" in overrides
        ? (overrides.user as ReportDownloadEntry["user"])
        : { id: "user-1", email: "alice@example.com" },
    report_type: overrides.report_type ?? "sbom",
    format: overrides.format ?? "cyclonedx-json",
    size_bytes:
      "size_bytes" in overrides
        ? (overrides.size_bytes as number | null)
        : 4096,
    created_at: overrides.created_at ?? "2026-05-26T12:00:00Z",
  };
}

function response(
  items: ReportDownloadEntry[],
  overrides: Partial<ReportHistoryResponse> = {},
): ReportHistoryResponse {
  return {
    items,
    total: overrides.total ?? items.length,
    page: overrides.page ?? 1,
    page_size: overrides.page_size ?? 50,
  };
}

// Tiny URL probe — `setSearchParams` writes to the router's location, and
// the assertions need to read the resulting query string. We mount a sibling
// component that mirrors the params into a `data-testid` element.
function UrlProbe() {
  const [searchParams] = useSearchParams();
  return (
    <div
      data-testid="url-probe"
      data-tab={searchParams.get("tab") ?? ""}
      data-scan={searchParams.get("scan") ?? ""}
      data-rpt-type={searchParams.get("rpt_type") ?? ""}
      data-rpt-page={searchParams.get("rpt_page") ?? ""}
    />
  );
}

function renderTab(initialEntries: string[] = ["/projects/proj-1"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route
            path="/projects/:id"
            element={
              <>
                <ReportsTab projectId="proj-1" />
                <UrlProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ReportsTab", () => {
  beforeEach(() => {
    mockedFetch.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders skeleton while loading and surfaces all four generate cards", () => {
    mockedFetch.mockReturnValue(new Promise(() => {}));
    renderTab();
    expect(screen.getByTestId("reports-history-loading")).toBeInTheDocument();
    // All four cards mount even during a pending history fetch — they are
    // navigation entry points, not data-dependent.
    expect(screen.getByTestId("reports-card-notice")).toBeInTheDocument();
    expect(screen.getByTestId("reports-card-sbom")).toBeInTheDocument();
    expect(screen.getByTestId("reports-card-vuln-pdf")).toBeInTheDocument();
    expect(screen.getByTestId("reports-card-vex")).toBeInTheDocument();
  });

  it("renders rows + pager once data arrives (happy path)", async () => {
    mockedFetch.mockResolvedValueOnce(
      response(
        [
          row({ id: "row-1", report_type: "notice", format: "text" }),
          row({ id: "row-2", report_type: "sbom", format: "cyclonedx-json" }),
          row({ id: "row-3", report_type: "vex_export", format: "cdx-vex" }),
        ],
        { total: 130, page: 1, page_size: 50 },
      ),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("reports-history-table")).toBeInTheDocument();
    });
    const rows = screen.getAllByTestId("reports-history-row");
    expect(rows).toHaveLength(3);
    // The pager shows "Page 1 of 3" (130 / 50 = 3 pages, ceil).
    expect(
      screen.getByTestId("reports-history-pagination"),
    ).toHaveTextContent(/Page 1 of 3/);
    // Type badge mirrors the row's report_type for every row.
    const badgeTypes = screen
      .getAllByTestId("reports-history-type-badge")
      .map((el) => el.getAttribute("data-report-type"));
    expect(badgeTypes).toEqual(["notice", "sbom", "vex_export"]);
  });

  it("renders the empty state when items is []", async () => {
    mockedFetch.mockResolvedValueOnce(response([], { total: 0 }));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("reports-history-empty")).toBeInTheDocument();
    });
    // No pager when zero rows.
    expect(
      screen.queryByTestId("reports-history-pagination"),
    ).not.toBeInTheDocument();
  });

  it("renders a generic 404 message without leaking permission semantics", async () => {
    mockedFetch.mockRejectedValueOnce(
      new ProblemError("not found", {
        status: 404,
        title: "Project Not Found",
        detail: "Project not found.",
        problem: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("reports-history-error")).toBeInTheDocument();
    });
    // The user-visible string is the generic "unavailable", not the
    // BE-side "Project Not Found" — existence-hide stays consistent on the
    // SPA surface.
    expect(
      screen.getByTestId("reports-history-error").textContent,
    ).toMatch(/Reports are unavailable/);
  });

  it("toggling the type filter refetches with the new params and writes ?rpt_type", async () => {
    mockedFetch.mockResolvedValue(response([row({ id: "row-1" })]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("reports-history-table")).toBeInTheDocument();
    });
    expect(mockedFetch).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ page: 1, pageSize: 50 }),
    );
    expect(mockedFetch).toHaveBeenLastCalledWith(
      "proj-1",
      expect.not.objectContaining({ types: expect.anything() }),
    );

    // Open the MultiSelect, then toggle `notice`.
    await userEvent.click(screen.getByTestId("reports-history-type-filter"));
    const noticeOption = await waitFor(() => {
      const opt = screen
        .getAllByTestId("reports-history-type-filter-option")
        .find((el) => el.getAttribute("data-value") === "notice");
      if (!opt) throw new Error("notice option not mounted");
      return opt;
    });
    await userEvent.click(noticeOption);

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ types: ["notice"], page: 1 }),
      );
    });
    expect(screen.getByTestId("url-probe").getAttribute("data-rpt-type")).toBe(
      "notice",
    );
  });

  it("clicking a generate-card deeplink sets ?tab=… and preserves ?scan=", async () => {
    mockedFetch.mockResolvedValueOnce(response([]));
    renderTab(["/projects/proj-1?tab=reports&scan=scan-pinned-id"]);
    await waitFor(() => {
      expect(screen.getByTestId("reports-history-empty")).toBeInTheDocument();
    });
    expect(screen.getByTestId("url-probe").getAttribute("data-scan")).toBe(
      "scan-pinned-id",
    );

    await userEvent.click(
      screen.getByTestId("reports-card-notice-deeplink"),
    );

    // The deeplink switches `?tab=obligations` but keeps `?scan=` pinned.
    expect(screen.getByTestId("url-probe").getAttribute("data-tab")).toBe(
      "obligations",
    );
    expect(screen.getByTestId("url-probe").getAttribute("data-scan")).toBe(
      "scan-pinned-id",
    );
  });

  it("clicking next pages and refetches with page=2 / writes ?rpt_page=2", async () => {
    mockedFetch.mockResolvedValueOnce(
      response([row({ id: "row-p1" })], { total: 130, page: 1 }),
    );
    mockedFetch.mockResolvedValueOnce(
      response([row({ id: "row-p2" })], { total: 130, page: 2 }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("reports-history-pagination")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("reports-history-next"));

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenLastCalledWith(
        "proj-1",
        expect.objectContaining({ page: 2, pageSize: 50 }),
      );
    });
    expect(screen.getByTestId("url-probe").getAttribute("data-rpt-page")).toBe(
      "2",
    );
  });

  it("renders 'Deleted user' when user is null and an em-dash for null scan / size", async () => {
    mockedFetch.mockResolvedValueOnce(
      response([
        row({
          id: "row-vex",
          report_type: "vex_export",
          user: null,
          scan_id: null,
          size_bytes: null,
          format: "cdx-vex",
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("reports-history-table")).toBeInTheDocument();
    });
    const rowEl = screen.getByTestId("reports-history-row");
    expect(rowEl.textContent).toMatch(/Deleted user/);
    // size + scan unknown both render as em-dash — exactly two in this row.
    expect(rowEl.textContent).toMatch(/—/);
  });
});
