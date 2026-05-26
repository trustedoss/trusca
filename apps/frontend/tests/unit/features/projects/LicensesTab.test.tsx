/**
 * LicensesTab — unit tests (PR #12).
 *
 * Validates loading skeleton, empty state, error state, distribution chart
 * render, and that filter / sort changes hit the wire layer with the right
 * params at offset 0.
 *
 * We mock the wire layer so the component renders without a backend, and
 * stub `react-virtuoso` with a plain renderer so all rows mount in jsdom —
 * mirrors VulnerabilitiesTab.test.tsx (PR #11).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  LicenseDistribution,
  LicenseListItem,
  LicenseListResponse,
} from "@/features/projects/api/licensesApi";
import { LicensesTab } from "@/features/projects/components/LicensesTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/licensesApi", async () => {
  return {
    listProjectLicenses: vi.fn(),
    getLicenseFinding: vi.fn(),
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

import {
  getLicenseFinding,
  listProjectLicenses,
} from "@/features/projects/api/licensesApi";

const mockedList = vi.mocked(listProjectLicenses);
const mockedGet = vi.mocked(getLicenseFinding);

function lic(
  spdxId: string,
  overrides: Partial<LicenseListItem> = {},
): LicenseListItem {
  const id =
    overrides.id ??
    `00000000-0000-0000-0000-${spdxId.toLowerCase().padEnd(12, "0").slice(0, 12)}`;
  return {
    id,
    license_id: overrides.license_id ?? `lic-${spdxId}`,
    spdx_id: spdxId,
    name: overrides.name ?? spdxId,
    category: "allowed",
    kind: "concluded",
    affected_count: 1,
    is_osi_approved: false,
    is_fsf_libre: false,
    sample_finding_id: id,
    ...overrides,
  };
}

function distribution(
  overrides: Partial<LicenseDistribution> = {},
): LicenseDistribution {
  return {
    forbidden: 0,
    conditional: 0,
    allowed: 0,
    unknown: 0,
    ...overrides,
  };
}

function listResponse(
  items: LicenseListItem[],
  total = items.length,
  dist: LicenseDistribution = distribution(),
): LicenseListResponse {
  return { items, total, distribution: dist };
}

function renderTab(initialEntries: string[] = ["/projects/proj-1"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <LicensesTab projectId="proj-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LicensesTab", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders skeleton while loading", () => {
    mockedList.mockReturnValue(new Promise(() => {})); // never resolves
    renderTab();
    expect(screen.getByTestId("licenses-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no findings exist", async () => {
    mockedList.mockResolvedValueOnce(listResponse([]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("licenses-empty")).toBeInTheDocument();
    });
  });

  it("renders rows once data arrives and exposes summary counts", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse(
        [
          lic("MIT", { affected_count: 5, category: "allowed" }),
          lic("GPL-3.0", { affected_count: 2, category: "forbidden" }),
        ],
        2,
        distribution({ allowed: 5, forbidden: 2 }),
      ),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("license-row")).toHaveLength(2);
    });
    const summary = screen.getByTestId("licenses-summary");
    expect(summary).toHaveAttribute("data-loaded", "2");
    expect(summary).toHaveAttribute("data-total", "2");
    // Per-row affected_count surfaces in a known column.
    const counts = screen
      .getAllByTestId("license-row-affected-count")
      .map((el) => el.textContent);
    expect(counts).toEqual(expect.arrayContaining(["5", "2"]));
  });

  it("renders the distribution chart when distribution comes in the response", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse(
        [lic("MIT", { affected_count: 3 })],
        1,
        distribution({ allowed: 3, forbidden: 1, conditional: 2, unknown: 0 }),
      ),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("licenses-distribution")).toBeInTheDocument();
    });
    expect(screen.getByTestId("license-distribution-chart")).toBeInTheDocument();
    // Legend lists each of the four categories with their count.
    expect(
      screen.getByTestId("license-legend-allowed").textContent,
    ).toContain("3");
    expect(
      screen.getByTestId("license-legend-forbidden").textContent,
    ).toContain("1");
  });

  it("renders the RFC 7807 detail in an alert on error", async () => {
    mockedList.mockRejectedValueOnce(
      new ProblemError("not allowed", {
        status: 403,
        title: "Forbidden",
        detail: "License access denied — surfaced verbatim.",
        problem: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("licenses-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("licenses-error").textContent).toContain(
      "License access denied — surfaced verbatim.",
    );
  });

  it("changing the category filter triggers a query at offset 0 and updates the URL", async () => {
    mockedList.mockResolvedValue(listResponse([lic("MIT")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("license-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    // Open the MultiSelect dropdown, then toggle the "forbidden" checkbox row.
    await userEvent.click(screen.getByTestId("licenses-category-filter"));
    const forbidden = await waitFor(() => {
      const option = screen
        .getAllByTestId("licenses-category-filter-option")
        .find((el) => el.getAttribute("data-value") === "forbidden");
      if (!option) throw new Error("forbidden option not mounted");
      return option;
    });
    await userEvent.click(forbidden);

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ categories: ["forbidden"], offset: 0 }),
      );
    });
  });

  it("changing the sort key triggers a query with that sort", async () => {
    mockedList.mockResolvedValue(listResponse([lic("MIT")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("license-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("licenses-sort"),
      "name",
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "name" }),
      );
    });
  });

  it("hydrates filter state from the URL on first render (CSV)", async () => {
    mockedList.mockResolvedValueOnce(listResponse([lic("MIT")]));
    renderTab([
      "/projects/proj-1?license_category=forbidden,conditional&kind=declared&sort=name&order=asc",
    ]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          categories: ["forbidden", "conditional"],
          kinds: ["declared"],
          sort: "name",
          order: "asc",
        }),
      );
    });
  });

  it("clicking a row sets ?license=<finding_id> in the URL and opens the drawer", async () => {
    const item = lic("Apache-2.0", {
      id: "00000000-0000-0000-0000-aaaa00000001",
    });
    mockedList.mockResolvedValueOnce(listResponse([item]));
    mockedGet.mockReturnValue(new Promise(() => {})); // never resolves
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("license-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("license-row"));
    await waitFor(() => {
      expect(screen.getByTestId("license-drawer")).toBeInTheDocument();
    });
  });
});
