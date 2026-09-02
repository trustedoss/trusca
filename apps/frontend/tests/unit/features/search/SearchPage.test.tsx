// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * SearchPage — unit tests (S3).
 *
 * The wire layer is mocked so these can assert the page's own behaviour: what
 * it asks the server for, what it does with the answer, and which of its
 * decisions live in the URL. The E2E suite covers the round trip against a
 * real backend.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SearchPage } from "@/features/search/SearchPage";
import type { SearchResultsPage } from "@/features/search/api/searchResultsApi";

vi.mock("@/features/search/api/searchResultsApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/search/api/searchResultsApi")
  >("@/features/search/api/searchResultsApi");
  return {
    ...actual,
    fetchSearchResults: vi.fn(),
    listSavedSearches: vi.fn(),
    createSavedSearch: vi.fn(),
    deleteSavedSearch: vi.fn(),
  };
});

vi.mock("@/features/search/api/externalAdvisoryApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/search/api/externalAdvisoryApi")
  >("@/features/search/api/externalAdvisoryApi");
  return {
    ...actual,
    lookupExternalAdvisory: vi.fn(),
  };
});

const useDeploymentFeatures = vi.fn();
vi.mock("@/features/about/api/useDeploymentFeatures", () => ({
  useDeploymentFeatures: () => useDeploymentFeatures(),
}));

const { fetchSearchResults, listSavedSearches, createSavedSearch } =
  await import("@/features/search/api/searchResultsApi");
const { lookupExternalAdvisory } = await import(
  "@/features/search/api/externalAdvisoryApi"
);

const mockedFetch = vi.mocked(fetchSearchResults);
const mockedSaved = vi.mocked(listSavedSearches);
const mockedCreate = vi.mocked(createSavedSearch);
const mockedAdvisory = vi.mocked(lookupExternalAdvisory);

function emptyPage(overrides: Partial<SearchResultsPage> = {}): SearchResultsPage {
  return {
    kind: "components",
    query: "",
    items_projects: [],
    items_components: [],
    items_vulnerabilities: [],
    items_licenses: [],
    total: 0,
    counts_capped: false,
    page: 1,
    size: 25,
    facets: {},
    ...overrides,
  };
}

function componentRow(name: string) {
  return {
    project_id: `p-${name}`,
    project_name: `project ${name}`,
    project_slug: `project-${name}`,
    component_id: `c-${name}`,
    component_name: name,
    version: "1.0.0",
    purl: `pkg:npm/${name}`,
    package_type: "npm",
  };
}

function renderPage(initialUrl = "/search?kind=components&q=lodash") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <SearchPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedSaved.mockResolvedValue({ items: [], total: 0, limit: 20 });
  mockedCreate.mockResolvedValue({
    id: "s-1",
    name: "n",
    kind: "components",
    params: {},
    created_at: new Date().toISOString(),
  });
  useDeploymentFeatures.mockReturnValue({ external_package_lookup: true });
  mockedAdvisory.mockResolvedValue({
    advisory_id: "not-called",
    found: false,
    title: null,
    cvss3_score: null,
    cvss3_vector: null,
    aliases: [],
  });
});

afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
});

describe("SearchPage", () => {
  it("asks the server for the kind and term the URL names", async () => {
    mockedFetch.mockResolvedValue(emptyPage({ query: "lodash" }));
    renderPage();

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "components", q: "lodash" }),
      );
    });
  });

  it("does not spend a request below the three-character threshold", async () => {
    mockedFetch.mockResolvedValue(emptyPage());
    renderPage("/search?kind=components&q=a");

    await waitFor(() => {
      expect(screen.getByTestId("search-summary")).toBeInTheDocument();
    });
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("does not spend a request at exactly 2 chars (concurrency-scaling Q1)", async () => {
    // The regression this pins: the floor moved from 2 to 3, so a 2-char
    // query (which used to fire) must now stay below threshold too.
    mockedFetch.mockResolvedValue(emptyPage());
    renderPage("/search?kind=components&q=lo");

    await waitFor(() => {
      expect(screen.getByTestId("search-summary")).toBeInTheDocument();
    });
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("does spend a request at exactly 3 chars", async () => {
    mockedFetch.mockResolvedValue(emptyPage({ query: "lod" }));
    renderPage("/search?kind=components&q=lod");

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "components", q: "lod" }),
      );
    });
  });

  it("renders the rows the server returned and reports the total", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({
        query: "lodash",
        items_components: [componentRow("lodash"), componentRow("lodash-es")],
        total: 2,
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("search-result-row")).toHaveLength(2);
    });
    expect(screen.getByTestId("search-summary")).toHaveAttribute(
      "data-total",
      "2",
    );
  });

  it("shows an exact total when the server did not cap the count", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({
        query: "lodash",
        items_components: [componentRow("lodash")],
        total: 2,
        counts_capped: false,
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("search-summary")).toHaveAttribute(
        "data-total-capped",
        "false",
      );
    });
    expect(screen.getByText("2 results")).toBeInTheDocument();
  });

  it("marks the total as a lower bound when the server capped the count (Q3)", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({
        query: "lodash",
        items_components: [componentRow("lodash")],
        total: 1000,
        counts_capped: true,
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("search-summary")).toHaveAttribute(
        "data-total-capped",
        "true",
      );
    });
    expect(screen.getByText("1000+ results")).toBeInTheDocument();
    // The exact-count phrasing must not also render alongside it.
    expect(screen.queryByText("1000 results")).not.toBeInTheDocument();
  });

  it("marks facet chip counts as a lower bound when the server capped the count (Q3)", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({
        kind: "vulnerabilities",
        query: "CVE",
        total: 1000,
        counts_capped: true,
        facets: { severity: [{ value: "critical", count: 1000 }] },
      }),
    );
    renderPage("/search?kind=vulnerabilities&q=CVE");

    const chip = await screen.findByTestId("search-facet-severity-critical");
    expect(within(chip).getByText("1000+")).toBeInTheDocument();
  });

  // The scope line states which scan(s) a tab reads. Since the
  // concurrency-scaling plan's Q2, only `projects` is not scan-scoped at all
  // (it matches the `projects` table directly); components, vulnerabilities,
  // and licences all read each project's current (latest succeeded) scan.
  // `as const` matters: without it the tuples widen to `string`, and
  // `emptyPage({ kind })` wants the literal union its wire type declares.
  it.each([
    ["components", "current_scan"],
    ["projects", "all_scans"],
    ["vulnerabilities", "current_scan"],
    ["licenses", "current_scan"],
  ] as const)("states that the %s tab covers %s", async (kind, scope) => {
    mockedFetch.mockResolvedValue(emptyPage({ kind, query: "lodash" }));
    renderPage(`/search?kind=${kind}&q=lodash`);

    await waitFor(() => {
      expect(screen.getByTestId("search-scope")).toHaveAttribute(
        "data-scope",
        scope,
      );
    });
  });

  it("states the scope before a term is typed", async () => {
    mockedFetch.mockResolvedValue(emptyPage());
    // Below the 3-char threshold nothing is fetched, but the tab still has to
    // say what it would search — that is when someone is choosing a tab.
    renderPage("/search?kind=vulnerabilities");

    await waitFor(() => {
      expect(screen.getByTestId("search-scope")).toHaveAttribute(
        "data-scope",
        "current_scan",
      );
    });
    expect(mockedFetch).not.toHaveBeenCalled();
  });

  it("switching tabs sheds the previous tab's facets", async () => {
    // package_type means nothing on the vulnerabilities tab, so carrying it
    // would filter by something the new tab cannot show the user.
    mockedFetch.mockResolvedValue(
      emptyPage({
        query: "lodash",
        items_components: [componentRow("lodash")],
        total: 1,
        facets: { package_type: [{ value: "npm", count: 1 }] },
      }),
    );
    renderPage("/search?kind=components&q=lodash&package_type=npm");

    await waitFor(() => {
      expect(screen.getByTestId("search-tabs")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("search-tab-vulnerabilities"));

    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "vulnerabilities", packageType: [] }),
      );
    });
  });

  it("a facet chip carries the server's count and toggles into the query", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({
        kind: "vulnerabilities",
        query: "CVE",
        items_vulnerabilities: [],
        total: 5,
        facets: {
          severity: [
            { value: "critical", count: 2 },
            { value: "high", count: 3 },
          ],
        },
      }),
    );
    renderPage("/search?kind=vulnerabilities&q=CVE");

    const chip = await screen.findByTestId("search-facet-severity-critical");
    // The count is the promise the click makes.
    expect(within(chip).getByText("2")).toBeInTheDocument();
    expect(chip).toHaveAttribute("data-active", "false");

    await userEvent.click(chip);
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        expect.objectContaining({ severity: ["critical"] }),
      );
    });
  });

  it("shows the pagination footer only when there is more than one page", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({ query: "lodash", items_components: [componentRow("a")], total: 5 }),
    );
    const { unmount } = renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("search-summary")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("search-pagination")).not.toBeInTheDocument();
    unmount();

    mockedFetch.mockResolvedValue(
      emptyPage({ query: "lodash", items_components: [componentRow("a")], total: 60 }),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("search-pagination")).toBeInTheDocument();
    });
  });

  it("paging forward asks for the next page", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({ query: "lodash", items_components: [componentRow("a")], total: 60 }),
    );
    renderPage();

    const next = await screen.findByTestId("search-page-next");
    await userEvent.click(next);
    await waitFor(() => {
      expect(mockedFetch).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2 }),
      );
    });
  });

  it("changing the term resets the page number", async () => {
    // Staying on page 4 of a set that just shrank shows an empty table.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedFetch.mockResolvedValue(
      emptyPage({ query: "lodash", items_components: [componentRow("a")], total: 60 }),
    );
    renderPage("/search?kind=components&q=lodash&page=3");

    await waitFor(() => {
      expect(screen.getByTestId("search-input")).toBeInTheDocument();
    });
    await userEvent.type(screen.getByTestId("search-input"), "x");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });

    await waitFor(() => {
      const calls = mockedFetch.mock.calls.map(([params]) => params);
      expect(calls.some((params) => params.q === "lodashx")).toBe(true);
    });
  });

  it("saves the current query string under a name", async () => {
    mockedFetch.mockResolvedValue(
      emptyPage({ query: "lodash", items_components: [componentRow("a")], total: 1 }),
    );
    renderPage("/search?kind=components&q=lodash&package_type=npm");

    await userEvent.click(await screen.findByTestId("search-save-trigger"));
    await userEvent.type(
      await screen.findByTestId("search-save-name"),
      "my search",
    );
    await userEvent.click(screen.getByTestId("search-save-confirm"));

    await waitFor(() => {
      expect(mockedCreate).toHaveBeenCalledWith(
        expect.objectContaining({
          name: "my search",
          kind: "components",
          params: expect.objectContaining({ q: "lodash", package_type: "npm" }),
        }),
      );
    });
  });

  it("disables saving once the per-user limit is reached", async () => {
    mockedSaved.mockResolvedValue({ items: [], total: 20, limit: 20 });
    mockedFetch.mockResolvedValue(
      emptyPage({ query: "lodash", items_components: [componentRow("a")], total: 1 }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("search-save-trigger")).toBeDisabled();
    });
  });

  it("surfaces a load failure instead of an empty table", async () => {
    mockedFetch.mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("search-error")).toBeInTheDocument();
    });
  });

  describe("external advisory card", () => {
    it("only calls the external advisory API for a CVE-shaped term on the vulnerabilities tab", async () => {
      mockedFetch.mockResolvedValue(emptyPage({ kind: "vulnerabilities", query: "lodash" }));
      renderPage("/search?kind=vulnerabilities&q=lodash");

      await waitFor(() => {
        expect(mockedFetch).toHaveBeenCalled();
      });
      expect(mockedAdvisory).not.toHaveBeenCalled();
    });

    it("does not call the external advisory API outside the vulnerabilities tab, even for a CVE-shaped term", async () => {
      mockedFetch.mockResolvedValue(
        emptyPage({ kind: "components", query: "CVE-2021-23337" }),
      );
      renderPage("/search?kind=components&q=CVE-2021-23337");

      await waitFor(() => {
        expect(mockedFetch).toHaveBeenCalled();
      });
      expect(mockedAdvisory).not.toHaveBeenCalled();
    });

    it("renders the advisory card when the term is CVE-shaped and the advisory is found", async () => {
      mockedFetch.mockResolvedValue(
        emptyPage({ kind: "vulnerabilities", query: "CVE-2021-23337" }),
      );
      mockedAdvisory.mockResolvedValue({
        advisory_id: "CVE-2021-23337",
        found: true,
        title: "lodash command injection",
        cvss3_score: 7.2,
        cvss3_vector: "CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H",
        aliases: ["GHSA-35jh-r3h4-6jhm"],
      });
      renderPage("/search?kind=vulnerabilities&q=CVE-2021-23337");

      await waitFor(() => {
        expect(mockedAdvisory).toHaveBeenCalledWith("CVE-2021-23337");
      });
      const card = await screen.findByTestId("search-advisory-card");
      expect(within(card).getByText("lodash command injection")).toBeInTheDocument();
      expect(within(card).getByText("GHSA-35jh-r3h4-6jhm")).toBeInTheDocument();
    });

    it("renders no card when the advisory is not found", async () => {
      mockedFetch.mockResolvedValue(
        emptyPage({ kind: "vulnerabilities", query: "CVE-9999-99999" }),
      );
      mockedAdvisory.mockResolvedValue({
        advisory_id: "CVE-9999-99999",
        found: false,
        title: null,
        cvss3_score: null,
        cvss3_vector: null,
        aliases: [],
      });
      renderPage("/search?kind=vulnerabilities&q=CVE-9999-99999");

      await waitFor(() => {
        expect(mockedAdvisory).toHaveBeenCalled();
      });
      expect(screen.queryByTestId("search-advisory-card")).not.toBeInTheDocument();
    });
  });
});
