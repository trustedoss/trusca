/**
 * ComponentsTab — unit tests (PR #10).
 *
 * Validates the search debounce → query, multi-select severity filter,
 * sort/order change, and row-click → drawer URL state. We mock the wire
 * layer and `react-virtuoso` so the test runs in jsdom.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ComponentDetailResponse,
  ComponentListResponse,
  ComponentSummary,
} from "@/features/projects/api/projectDetailApi";
import { ComponentsTab } from "@/features/projects/components/ComponentsTab";

vi.mock("@/features/projects/api/projectDetailApi", async () => {
  return {
    getProjectOverview: vi.fn(),
    listProjectComponents: vi.fn(),
    getComponent: vi.fn(),
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
  getComponent,
  listProjectComponents,
} from "@/features/projects/api/projectDetailApi";

const mockedList = vi.mocked(listProjectComponents);
const mockedGet = vi.mocked(getComponent);

function comp(
  name: string,
  overrides: Partial<ComponentSummary> = {},
): ComponentSummary {
  const id =
    overrides.id ??
    `00000000-0000-0000-0000-${name.padEnd(12, "0").slice(0, 12)}`;
  return {
    id,
    component_id: id,
    name,
    version: "1.0.0",
    purl: `pkg:npm/${name}@1.0.0`,
    license: "MIT",
    license_category: "allowed",
    severity_max: "low",
    vulnerability_count: 0,
    ...overrides,
  };
}

function listResponse(
  items: ComponentSummary[],
  total = items.length,
  offset = 0,
  limit = 100,
): ComponentListResponse {
  return { items, total, limit, offset };
}

function detail(
  overrides: Partial<ComponentDetailResponse> = {},
): ComponentDetailResponse {
  return {
    id: "00000000-0000-0000-0000-alpha0000000",
    project_id: "11111111-1111-1111-1111-111111111111",
    name: "Alpha",
    version: "1.0.0",
    purl: "pkg:npm/alpha@1.0.0",
    license: "MIT",
    license_category: "allowed",
    severity_max: "low",
    vulnerabilities: [],
    raw_data: { source: "cdxgen" },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function renderTab(initialEntries: string[] = ["/projects/proj-1"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <ComponentsTab projectId="proj-1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ComponentsTab", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders skeleton while loading and rows once data arrives", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([comp("Alpha"), comp("Bravo")]),
    );
    renderTab();
    expect(screen.getByTestId("components-loading")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(2);
    });
    const summary = screen.getByTestId("components-summary");
    expect(summary).toHaveAttribute("data-loaded", "2");
    expect(summary).toHaveAttribute("data-total", "2");
  });

  it("renders the empty state when no components match", async () => {
    mockedList.mockResolvedValueOnce(listResponse([]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("components-empty")).toBeInTheDocument();
    });
  });

  it("debounces the search input then refetches with the new query", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });
    expect(mockedList).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ search: undefined }),
    );

    const search = screen.getByTestId("components-search");
    await userEvent.type(search, "alp");
    // Before the debounce window elapses, no extra fetch has happened.
    expect(mockedList).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ search: "alp" }),
      );
    });
  });

  it("changing the severity filter triggers a fresh query at offset 0", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    const select = screen.getByTestId(
      "components-severity-filter",
    ) as HTMLSelectElement;
    // Select critical via DOM API since multi-selects are awkward to drive.
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

  it("changing the sort key triggers a query with that sort", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("components-sort"),
      "severity",
    );

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "severity" }),
      );
    });
  });

  it("clicking a row opens the drawer and fetches the detail", async () => {
    const alpha = comp("Alpha", {
      id: "00000000-0000-0000-0000-alpha0000000",
    });
    mockedList.mockResolvedValueOnce(listResponse([alpha]));
    mockedGet.mockResolvedValueOnce(detail({ id: alpha.id, name: "Alpha" }));

    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("component-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("component-row"));

    await waitFor(() => {
      expect(screen.getByTestId("component-drawer")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith(alpha.id);
    });
    await waitFor(() => {
      expect(screen.getByTestId("component-drawer-meta")).toBeInTheDocument();
    });
  });

  it("hydrates filter state from the URL on first render", async () => {
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab(["/projects/proj-1?severity=critical,high&sort=severity&order=desc"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          severity: ["critical", "high"],
          sort: "severity",
          order: "desc",
        }),
      );
    });
  });
});
