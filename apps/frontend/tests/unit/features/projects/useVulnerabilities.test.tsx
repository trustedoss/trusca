/**
 * useVulnerabilities — pagination contract.
 *
 * The findings list was a single-page query for a long time, which meant the
 * 101st row of a filtered result could not be reached: `?page=` was written
 * into the URL but nothing incremented it and no control changed it. The tab
 * drives paging through Virtuoso's `endReached`, which needs a measured
 * viewport jsdom will not give it, so the contract is pinned on the hook
 * instead of through the component.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useVulnerabilities } from "@/features/projects/api/useVulnerabilities";
import type {
  VulnerabilityListItem,
  VulnerabilityListResponse,
} from "@/features/projects/api/vulnerabilitiesApi";

vi.mock("@/features/projects/api/vulnerabilitiesApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/vulnerabilitiesApi")
  >("@/features/projects/api/vulnerabilitiesApi");
  return { ...actual, listProjectVulnerabilities: vi.fn() };
});

import { listProjectVulnerabilities } from "@/features/projects/api/vulnerabilitiesApi";

const mockedList = vi.mocked(listProjectVulnerabilities);

function row(id: string): VulnerabilityListItem {
  return {
    id,
    cve_id: `CVE-2024-${id}`,
    severity: "high",
    status: "new",
    component_name: "pkg",
    component_version: "1.0.0",
    cvss_score: 7.5,
    epss_score: null,
    epss_percentile: null,
    kev: false,
    kev_due_date: null,
    reachable: null,
    summary: null,
    discovered_at: null,
    sla_due_at: null,
    sla_status: null,
    analysis_source: null,
    fixed_version: null,
  } as unknown as VulnerabilityListItem;
}

function page(
  items: VulnerabilityListItem[],
  total: number,
  offset: number,
): VulnerabilityListResponse {
  return { items, total, limit: 100, offset };
}

const FILTERS = {
  search: "",
  severity: [],
  status: [],
  sort: "severity" as const,
  order: "desc" as const,
  min_epss: null,
  reachable: null,
  sla: null,
  license_category: [],
  limit: 100,
};

/**
 * A client per test, and the wrapper built around that client. Newing the
 * client up inside the wrapper function instead throws the cache away on
 * every re-render, so a second page can never accumulate and the failure
 * looks like the hook's.
 */
function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, wrapper: Wrapper };
}

beforeEach(() => {
  mockedList.mockReset();
});

describe("useVulnerabilities", () => {
  it("offers a next page while the server says rows remain", async () => {
    mockedList.mockResolvedValueOnce(
      page([row("1"), row("2")], 150, 0),
    );

    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useVulnerabilities("proj-1", FILTERS),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);
  });

  it("fetches the next page from the consumed offset", async () => {
    const first = Array.from({ length: 100 }, (_, i) => row(String(i)));
    // Answer by offset rather than by call order: a `Once` queue makes the
    // test depend on how many times the query happens to run.
    mockedList.mockImplementation((_projectId, options) =>
      Promise.resolve(
        (options?.offset ?? 0) === 0
          ? page(first, 150, 0)
          : page([row("100")], 150, 100),
      ),
    );

    const { client, wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useVulnerabilities("proj-1", FILTERS),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    await act(async () => {
      await result.current.fetchNextPage();
    });

    // Assert on the cache rather than the hook's last render: the render that
    // carries the second page can land after the awaited fetch resolves, and
    // the cache is what the table reads either way.
    const cached = () =>
      client.getQueriesData({ queryKey: ["projects"] })[0]?.[1] as
        | { pages: VulnerabilityListResponse[] }
        | undefined;

    await waitFor(() => expect(cached()?.pages).toHaveLength(2));

    expect(mockedList).toHaveBeenNthCalledWith(
      2,
      "proj-1",
      expect.objectContaining({ offset: 100, limit: 100 }),
    );
    // Row 101 is now in the flattened list, which is the whole point.
    const all = cached()!.pages.flatMap((p) => p.items);
    expect(all).toHaveLength(101);
  });

  it("stops offering pages once every row is loaded", async () => {
    mockedList.mockResolvedValueOnce(page([row("1"), row("2")], 2, 0));

    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useVulnerabilities("proj-1", FILTERS),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
  });

  it("does not run without a project id", () => {
    const { wrapper } = makeWrapper();
    renderHook(() => useVulnerabilities(undefined, FILTERS), { wrapper });
    expect(mockedList).not.toHaveBeenCalled();
  });
});
