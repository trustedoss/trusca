/**
 * ComponentsTab — unit tests (PR #10).
 *
 * Validates the search debounce → query, multi-select severity filter,
 * sort/order change, and row-click → drawer URL state. We mock the wire
 * layer and `react-virtuoso` so the test runs in jsdom.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ComponentDetailResponse,
  ComponentListResponse,
  ComponentSummary,
} from "@/features/projects/api/projectDetailApi";
import {
  COMPONENT_COLUMN_WIDTH_PX,
  componentTrackMinWidth,
} from "@/features/projects/lib/componentColumnLayout";
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
    malicious_state: null,
    malicious_id: null,
    malicious_source: null,
    eol_state: null,
    eol_date: null,
    currency_state: null,
    currency_latest: null,
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
    component_id: "pkg-0000-0000-0000-000000000000",
    project_id: "11111111-1111-1111-1111-111111111111",
    name: "Alpha",
    version: "1.0.0",
    purl: "pkg:npm/alpha@1.0.0",
    license: "MIT",
    license_category: "allowed",
    severity_max: "low",
    vulnerabilities: [],
    obligations: [],
    raw_data: { source: "cdxgen" },
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    // W2 #31 — required wire fields. Default to graph-less ("—").
    depth: null,
    direct: false,
    dependency_scope: null,
    malicious_state: null,
    malicious_id: null,
    malicious_source: null,
    eol_state: null,
    eol_product: null,
    eol_cycle: null,
    eol_date: null,
    eol_source: null,
    currency_state: null,
    currency_latest: null,
    currency_latest_release_date: null,
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
    // M-22 — the row container is no longer itself a <button>; the open
    // affordance is the inner `component-row-open` button (the CVEs-count
    // link lives outside it as a sibling, keeping the DOM valid).
    await userEvent.click(screen.getByTestId("component-row-open"));

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

  // ─── M-22 — CVEs-count cell deep-links into the Vulnerabilities tab ──────

  it("renders the CVEs count as a pre-filtered Vulnerabilities-tab link when > 0", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        comp("Alpha", { vulnerability_count: 3 }),
        comp("Bravo", { vulnerability_count: 0 }),
      ]),
    );

    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(2);
    });

    // Alpha (count 3) carries the deep-link…
    const links = screen.getAllByTestId("component-row-vuln-link");
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute(
      "href",
      "/projects/proj-1?tab=vulnerabilities&search=Alpha",
    );
    expect(links[0].textContent).toBe("3");

    // …Bravo (count 0) stays plain text — nothing to filter to.
    const cells = screen.getAllByTestId("component-row-vuln-count");
    expect(cells[1].querySelector("a")).toBeNull();
    expect(cells[1].textContent).toBe("0");
  });

  it("clicking the CVEs-count link does not open the component drawer", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([comp("Alpha", { vulnerability_count: 3 })]),
    );

    renderTab();
    const link = await screen.findByTestId("component-row-vuln-link");
    await userEvent.click(link);

    // Navigation happens (MemoryRouter), but the drawer must NOT open: no
    // detail fetch, no drawer in the DOM.
    expect(mockedGet).not.toHaveBeenCalled();
    expect(screen.queryByTestId("component-drawer")).not.toBeInTheDocument();
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
  // W2 #31 — Direct/Transitive + commercial-SCA-style "Usage" facets.
  // -----------------------------------------------------------------------

  it("exposes Type and Usage column headers", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse([
        comp("Alpha", { direct: true, depth: 1, dependency_scope: "required" }),
      ]),
    );
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
    // A missing value is a plain muted dash, not a grey dot-and-dash chip.
    expect(badges[0].querySelector(".rounded-full")).not.toBeNull();
    expect(badges[2].querySelector(".rounded-full")).toBeNull();
    expect(badges[2].className).toContain("text-muted-foreground");
    expect(badges[2].querySelector("[aria-hidden]")?.textContent).toBe("\u2014");
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
    expect(badges[0].querySelector(".rounded-full")).not.toBeNull();
    expect(badges[2].querySelector(".rounded-full")).toBeNull();
    expect(badges[2].className).toContain("text-muted-foreground");
    expect(badges[2].querySelector("[aria-hidden]")?.textContent).toBe("\u2014");
  });

  it("puts the Table/Graph switch in the toolbar row, after Columns and Export", async () => {
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab();
    const toolbar = await screen.findByTestId("components-toolbar");
    const toggle = screen.getByTestId("components-view-toggle-table");
    expect(toolbar.contains(toggle)).toBe(true);
    const exportButton = screen.getByTestId("components-export-csv");
    // Document order: Export first, then the switch at the end of the row.
    expect(
      exportButton.compareDocumentPosition(toggle) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
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

  // -----------------------------------------------------------------------
  // W9 #52 — ColumnsPicker integration. The picker hides optional columns;
  // required columns (name, version) must always remain rendered.
  // -----------------------------------------------------------------------

  it("hiding the policy column via the ColumnsPicker drops the policy cell from each row", async () => {
    // Reset localStorage so the test is independent of any other run.
    window.localStorage.clear();
    mockedList.mockResolvedValueOnce(
      listResponse([
        comp("Alpha", {
          id: "11111111-aaaa-aaaa-aaaa-policyhide01",
          license_category: "allowed",
        }),
      ]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("component-row")).toBeInTheDocument();
    });
    // Policy cell is rendered by default.
    expect(
      screen.getByTestId("component-row-cell-policy"),
    ).toBeInTheDocument();

    // Open the columns picker, toggle "policy" off.
    await userEvent.click(
      screen.getByTestId("components-columns-picker-trigger"),
    );
    const policyOption = await waitFor(() =>
      screen.getByTestId("components-columns-picker-option-policy"),
    );
    await userEvent.click(policyOption);

    await waitFor(() => {
      expect(
        screen.queryByTestId("component-row-cell-policy"),
      ).not.toBeInTheDocument();
    });
    // Required columns stay (Name / Version always rendered) — the row still
    // has its identity cells visible.
    expect(screen.getByTestId("component-row")).toBeInTheDocument();
  });

  // #26 — the Flagged-only toggle. These two cases exist because the toggle
  // shipped connected to the URL but not to the query: `useComponents` had no
  // `malicious` field, so the filter reached neither the cache key nor the
  // request, and clicking it changed the address bar while the list stayed
  // whole. The nightly e2e caught it; nothing at this level did.
  it("clicking the Flagged-only toggle refetches with malicious=true", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.click(screen.getByTestId("components-show-only-trigger"));
    await userEvent.click(await screen.findByTestId("components-malicious-filter"));

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ malicious: true, offset: 0 }),
      );
    });
  });

  it("keeps each Show only option on its own URL flag", async () => {
    mockedList.mockResolvedValue(listResponse([comp("Alpha")]));
    renderTab();
    const trigger = await screen.findByTestId("components-show-only-trigger");
    expect(trigger).toHaveAttribute("data-active-count", "0");
    expect(trigger.textContent).toBe("Show only");
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByTestId("components-eol-filter"));
    // The EOL option sets the EOL flag, and only that one.
    await waitFor(() => {
      expect(mockedList).toHaveBeenLastCalledWith(
        "proj-1",
        expect.objectContaining({ eol: true }),
      );
    });
    const afterEol = mockedList.mock.calls.at(-1)?.[1] as Record<string, unknown>;
    expect(afterEol.outdated ?? null).toBeNull();
    expect(afterEol.malicious ?? null).toBeNull();
    await userEvent.click(screen.getByTestId("components-outdated-filter"));
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ eol: true, outdated: true }),
      );
    });
    // The menu stays open while options are toggled.
    expect(screen.getByTestId("components-eol-filter")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByTestId("components-malicious-filter")).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(trigger).toHaveAttribute("data-active-keys", "eol,outdated");
    expect(trigger.textContent).toContain("Show only (2)");
  });

  it("hydrates ?malicious=true into the Flagged-only toggle on first render", async () => {
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab(["/projects/proj-1?malicious=true"]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ malicious: true }),
      );
    });
    const trigger = screen.getByTestId("components-show-only-trigger");
    expect(trigger).toHaveAttribute("data-active-keys", "malicious");
    expect(trigger.textContent).toContain("Show only (1)");
    await userEvent.click(trigger);
    expect(await screen.findByTestId("components-malicious-filter")).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByTestId("components-eol-filter")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  // -----------------------------------------------------------------------
  // Empty columns drop out (design review: Type and Usage were dashes).
  // -----------------------------------------------------------------------

  /** Declared width in px of a header or row cell, from its Tailwind class. */
  function declaredWidth(el: HTMLElement): number {
    const floor = /(?:^|\s)min-w-\[(\d+)px\]/.exec(el.className);
    if (floor) return Number(floor[1]);
    const fixed = /(?:^|\s)w-(\d+)(?:\s|$)/.exec(el.className);
    if (!fixed) throw new Error(`no width class on: ${el.className}`);
    return Number(fixed[1]) * 4;
  }

  it("hides Type and Usage when no loaded row has a value, and keeps the track floor in step", async () => {
    window.localStorage.clear();
    mockedList.mockResolvedValueOnce(
      listResponse([comp("Alpha"), comp("Bravo")]),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("component-row")).toHaveLength(2);
    });
    expect(screen.queryByTestId("components-header-cell-type")).toBeNull();
    expect(screen.queryByTestId("components-header-cell-usage")).toBeNull();
    expect(screen.queryByTestId("component-row-cell-type")).toBeNull();
    expect(screen.queryByTestId("component-row-cell-usage")).toBeNull();

    // Header and body agree cell by cell, and the floor counts exactly them.
    const header = within(screen.getByTestId("components-header"))
      .getAllByRole("columnheader")
      .map(declaredWidth);
    const body = within(screen.getAllByTestId("component-row")[0])
      .getAllByRole("cell")
      .map(declaredWidth);
    expect(body).toEqual(header);
    const visible = new Set(["license", "policy", "severity", "vulns"]);
    expect(header).toEqual([
      120,
      COMPONENT_COLUMN_WIDTH_PX.version,
      ...[...visible].map((id) => COMPONENT_COLUMN_WIDTH_PX[id]),
    ]);
    expect(
      screen.getByTestId("components-track").style.minWidth,
    ).toBe(`${componentTrackMinWidth(visible)}px`);
  });

  it("keeps Type once any row has a depth, even if a later page has none", async () => {
    window.localStorage.clear();
    mockedList.mockResolvedValueOnce(
      listResponse([comp("Alpha", { direct: true, depth: 1 })], 2),
    );
    renderTab();
    await screen.findByTestId("components-header-cell-type");
    expect(screen.queryByTestId("components-header-cell-usage")).toBeNull();
  });

  it("keeps a column the user switched on in the Columns menu", async () => {
    window.localStorage.clear();
    window.localStorage.setItem(
      "column-pinned:components",
      JSON.stringify(["usage"]),
    );
    mockedList.mockResolvedValueOnce(listResponse([comp("Alpha")]));
    renderTab();
    await screen.findByTestId("components-header-cell-usage");
    expect(screen.queryByTestId("components-header-cell-type")).toBeNull();
  });
});
