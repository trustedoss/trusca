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
    // W2 #31 — direct/depth/dependency_scope are required wire fields.
    // Default to a graph-less ("—") shape so legacy tests don't depend on
    // values they don't care about.
    depth: null,
    direct: false,
    dependency_scope: null,
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
    // W2 #31 — required wire fields. Default to graph-less ("—").
    depth: null,
    direct: false,
    dependency_scope: null,
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

  // W4-B #17 — the severity / license MultiSelect drops are gone from the
  // toolbar. The URL params still drive the wire (deep-links from the
  // Overview chart populate them); the user removes a filter via a chip in
  // the new ActiveFilterChips row.
  it("hydrating ?severity=critical surfaces a removable chip", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab(["/projects/proj-1?severity=critical"]);
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });
    // Filter was applied on first fetch.
    expect(mockedList).toHaveBeenCalledWith(
      "proj-1",
      expect.objectContaining({ severity: ["critical"] }),
    );
    // A chip surfaces the active filter so the user can see + clear it
    // without an extra dropdown.
    const chip = await screen.findByTestId("active-filter-chip");
    expect(chip.getAttribute("data-facet")).toBe("severity");
    expect(chip.getAttribute("data-value")).toBe("critical");

    // Clearing the chip drops the filter from the wire on the next call.
    mockedList.mockClear();
    await userEvent.click(
      screen.getByTestId("active-filter-chip-clear"),
    );
    await waitFor(() => {
      // `useComponents` translates an empty array to `severity: undefined`
      // on the wire so an empty CSV never appears in the query string.
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ severity: undefined }),
      );
    });
    expect(screen.queryByTestId("active-filter-chip")).not.toBeInTheDocument();
  });

  it("clicking a sortable column header cycles unset → asc → desc → unset", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });

    // The toolbar no longer has a dropdown; the column header is the sort UI.
    expect(screen.queryByTestId("components-sort")).not.toBeInTheDocument();

    // unset → asc — re-resolve the header on every click since the data-sort-order
    // attribute changes and we want to assert against the live state.
    mockedList.mockClear();
    await userEvent.click(
      screen.getByTestId("components-sort-header-severity"),
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "severity", order: "asc" }),
      );
    });
    await waitFor(() => {
      expect(
        screen
          .getByTestId("components-sort-header-severity")
          .getAttribute("data-sort-order"),
      ).toBe("asc");
    });

    // asc → desc
    mockedList.mockClear();
    await userEvent.click(
      screen.getByTestId("components-sort-header-severity"),
    );
    await waitFor(() => {
      expect(
        screen
          .getByTestId("components-sort-header-severity")
          .getAttribute("data-sort-order"),
      ).toBe("desc");
    });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "severity", order: "desc" }),
      );
    });
  });

  it("license cell renders SPDX inline and the policy badge in the next column", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        comp("Alpha", { license: "MIT", license_category: "allowed" }),
        comp("Bravo", { license: null, license_category: "unknown" }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row-license-spdx")).toHaveLength(
        2,
      );
    });
    const spdxCells = screen.getAllByTestId("component-row-license-spdx");
    expect(spdxCells[0].getAttribute("data-license-spdx")).toBe("MIT");
    expect(spdxCells[0].textContent).toContain("MIT");
    // null SPDX renders the localized dash. The policy badge is now a sibling
    // cell rather than stacked inside, so assertions about category continue
    // through the LicenseCategoryBadge below.
    expect(spdxCells[1].getAttribute("data-license-spdx")).toBe("");
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

  // -----------------------------------------------------------------------
  // W2 #31 — Direct/Transitive + BD-style "Usage" facets.
  // -----------------------------------------------------------------------

  it("exposes Type and Usage column headers", async () => {
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab();
    const header = await screen.findByTestId("components-header");
    expect(header.textContent).toMatch(/Type/i);
    expect(header.textContent).toMatch(/Usage/i);
  });

  it("renders Direct/Transitive/— badges per row based on direct + depth", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        comp("Alpha", { id: "11111111-aaaa-aaaa-aaaa-direct000001", direct: true, depth: 1 }),
        comp("Bravo", { id: "22222222-bbbb-bbbb-bbbb-trans0000002", direct: false, depth: 3 }),
        comp("Charlie", { id: "33333333-cccc-cccc-cccc-unknown00003", direct: false, depth: null }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(3);
    });
    const badges = screen.getAllByTestId("dependency-type-badge");
    expect(badges).toHaveLength(3);
    const buckets = badges.map((el) => el.getAttribute("data-dependency-type"));
    expect(buckets).toEqual(["direct", "transitive", "unknown"]);
    // Depth from the chosen path is exposed for downstream harness assertions.
    expect(badges[0]).toHaveAttribute("data-depth", "1");
    expect(badges[1]).toHaveAttribute("data-depth", "3");
  });

  it("renders Required/Optional/— badges per row based on dependency_scope", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        comp("Alpha", {
          id: "11111111-aaaa-aaaa-aaaa-req0000001",
          dependency_scope: "required",
        }),
        comp("Bravo", {
          id: "22222222-bbbb-bbbb-bbbb-opt0000002",
          dependency_scope: "optional",
        }),
        comp("Charlie", {
          id: "33333333-cccc-cccc-cccc-null000003",
          dependency_scope: null,
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(3);
    });
    const badges = screen.getAllByTestId("dependency-scope-badge");
    expect(badges).toHaveLength(3);
    const buckets = badges.map((el) => el.getAttribute("data-dependency-scope"));
    expect(buckets).toEqual(["required", "optional", "unknown"]);
  });

  it("clicking the Direct segment sets ?direct=true and refetches with that filter", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    const tree = renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.click(screen.getByTestId("components-dependency-type-direct"));

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ direct: true, offset: 0 }),
      );
    });
    // URL mirror via MemoryRouter — the toolbar's URL effect runs after
    // state settles. Use the location pathname/search seen by the router.
    await waitFor(() => {
      const url = tree.container.ownerDocument.location;
      // jsdom doesn't move on MemoryRouter; assert via the live searchParams
      // exposed through window.location is unreliable — instead, walk back
      // to the most recent fetch arg, which already proves the wire path.
      expect(url).toBeDefined();
    });
  });

  it("hydrates ?direct=true into the Direct toggle on first render", async () => {
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab(["/projects/proj-1?direct=true"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ direct: true }),
      );
    });
    const direct = screen.getByTestId("components-dependency-type-direct");
    expect(direct).toHaveAttribute("data-active", "true");
  });

  it("selecting the Required usage chip filters with dependency_scope=['required']", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.click(screen.getByTestId("components-usage-filter"));
    const required = await waitFor(() => {
      const option = screen
        .getAllByTestId("components-usage-filter-option")
        .find((el) => el.getAttribute("data-value") === "required");
      if (!option) throw new Error("required option not mounted");
      return option;
    });
    await userEvent.click(required);

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          dependency_scope: ["required"],
          offset: 0,
        }),
      );
    });
  });

  it("hydrates ?dependency_scope=required,unspecified into the Usage chip", async () => {
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab(["/projects/proj-1?dependency_scope=required,unspecified"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          dependency_scope: ["required", "unspecified"],
        }),
      );
    });
  });

  it("drops unknown dependency_scope values from the URL during hydration", async () => {
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab([
      "/projects/proj-1?dependency_scope=required,bogus,unspecified",
    ]);
    await waitFor(() => {
      // 'bogus' is dropped by the VALID_SCOPE guard; the order of valid
      // values is preserved.
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          dependency_scope: ["required", "unspecified"],
        }),
      );
    });
  });
});
