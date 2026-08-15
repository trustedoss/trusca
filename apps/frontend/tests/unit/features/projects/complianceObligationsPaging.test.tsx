/**
 * useCompliance / useObligations, pagination contract.
 *
 * Both grids paged at 100 and wrote a page parameter nothing incremented, so
 * the 101st licence and the 101st obligation could not be reached. Pinned on
 * the hooks because Virtuoso's `endReached` needs a measured viewport that
 * jsdom does not provide.
 *
 * The two differ in one way worth keeping visible: the compliance endpoint
 * echoes `offset` back in its response and the obligations endpoint does not,
 * so the latter counts the next offset from the request it made.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useCompliance } from "@/features/projects/api/useCompliance";
import { useObligations } from "@/features/projects/api/useObligations";

vi.mock("@/features/projects/api/complianceApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/complianceApi")
  >("@/features/projects/api/complianceApi");
  return { ...actual, listProjectCompliance: vi.fn() };
});
vi.mock("@/features/projects/api/obligationsApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/obligationsApi")
  >("@/features/projects/api/obligationsApi");
  return { ...actual, listProjectObligations: vi.fn() };
});

import { listProjectCompliance } from "@/features/projects/api/complianceApi";
import { listProjectObligations } from "@/features/projects/api/obligationsApi";

const mockedCompliance = vi.mocked(listProjectCompliance);
const mockedObligations = vi.mocked(listProjectObligations);

/**
 * A client per test, built around the wrapper rather than inside it: a
 * wrapper that news one up per render throws the cache away on every
 * re-render, so pages never accumulate and the hook takes the blame.
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

function cachedPages(client: QueryClient, key: string) {
  const entry = client.getQueriesData({ queryKey: ["projects", "p1", key] })[0];
  return (entry?.[1] as { pages?: unknown[] } | undefined)?.pages;
}

const COMPLIANCE_FILTERS = {
  search: "",
  categories: [],
  kinds: [],
  hasObligations: null,
  sort: "category" as const,
  order: "desc" as const,
  limit: 100,
};

const OBLIGATION_FILTERS = {
  search: "",
  kinds: [],
  categories: [],
  sort: "category" as const,
  order: "desc" as const,
  limit: 100,
};

beforeEach(() => {
  mockedCompliance.mockReset();
  mockedObligations.mockReset();
});

describe("useCompliance", () => {
  it("fetches the next page from the offset the server reports", async () => {
    const rows = (n: number, prefix: string) =>
      Array.from({ length: n }, (_, i) => ({ license_finding_id: `${prefix}${i}` }));
    mockedCompliance.mockImplementation((_p, o) =>
      Promise.resolve({
        items: (o?.offset ?? 0) === 0 ? rows(100, "a") : rows(20, "b"),
        total: 120,
        limit: 100,
        offset: o?.offset ?? 0,
        declared_license: null,
        conflict_summary: null,
      } as never),
    );

    const { client, wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useCompliance("p1", COMPLIANCE_FILTERS),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => {
      await result.current.fetchNextPage();
    });

    await waitFor(() => expect(cachedPages(client, "compliance")).toHaveLength(2));
    expect(mockedCompliance).toHaveBeenNthCalledWith(
      2,
      "p1",
      expect.objectContaining({ offset: 100 }),
    );
  });

  it("stops once every row is loaded", async () => {
    mockedCompliance.mockResolvedValue({
      items: [{ license_finding_id: "only" }],
      total: 1,
      limit: 100,
      offset: 0,
      declared_license: null,
      conflict_summary: null,
    } as never);

    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useCompliance("p1", COMPLIANCE_FILTERS),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
  });
});

describe("useObligations", () => {
  it("counts the next offset from the request, since the response omits it", async () => {
    const rows = (n: number, prefix: string) =>
      Array.from({ length: n }, (_, i) => ({ id: `${prefix}${i}` }));
    mockedObligations.mockImplementation((_p, o) =>
      Promise.resolve({
        items: (o?.offset ?? 0) === 0 ? rows(100, "a") : rows(5, "b"),
        total: 105,
        distribution: {},
      } as never),
    );

    const { client, wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useObligations("p1", OBLIGATION_FILTERS),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);

    await act(async () => {
      await result.current.fetchNextPage();
    });

    await waitFor(() =>
      expect(cachedPages(client, "obligations")).toHaveLength(2),
    );
    expect(mockedObligations).toHaveBeenNthCalledWith(
      2,
      "p1",
      expect.objectContaining({ offset: 100 }),
    );
  });

  it("stops once every row is loaded", async () => {
    mockedObligations.mockResolvedValue({
      items: [{ id: "only" }],
      total: 1,
      distribution: {},
    } as never);

    const { wrapper } = makeWrapper();
    const { result } = renderHook(
      () => useObligations("p1", OBLIGATION_FILTERS),
      { wrapper },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(false);
  });
});
