// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * InventoryPage: unit tests for the empty state.
 *
 * Concurrency-scaling plan Q2 (2026-08-22): the search page used to reach
 * back through a project's whole scan history, so a term that missed here
 * (latest-scan-only) could still be found there, and the empty state offered
 * a "Search every scan" link to it. Q2 narrowed search to the current scan
 * too, so the link would always land on another empty result and was
 * removed. The empty state is now generic regardless of whether a search
 * term is active.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InventoryPage } from "@/features/inventory/InventoryPage";
import type { InventoryComponentListResponse } from "@/features/inventory/api/inventoryApi";

vi.mock("@/features/inventory/api/inventoryApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/inventory/api/inventoryApi")
  >("@/features/inventory/api/inventoryApi");
  return {
    ...actual,
    listInventoryComponents: vi.fn(),
    listComponentUsage: vi.fn(),
    listVulnerabilityImpact: vi.fn(),
  };
});

const { listInventoryComponents } = await import(
  "@/features/inventory/api/inventoryApi"
);
const mockedList = vi.mocked(listInventoryComponents);

function emptyResponse(): InventoryComponentListResponse {
  return { items: [], total: 0, limit: 50, offset: 0 };
}

function renderPage(initialUrl = "/components") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialUrl]}>
        <InventoryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockedList.mockResolvedValue(emptyResponse());
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("InventoryPage empty state", () => {
  it("shows the generic empty state when a term finds nothing", async () => {
    renderPage("/components?inv_search=lodash");

    await waitFor(() => {
      expect(screen.getByTestId("inventory-empty")).toBeInTheDocument();
    });

    // No cross-surface offer: since Q2, the search page reads the same
    // latest-scan-only scope this page does, so there is nothing else to try.
    expect(
      screen.queryByTestId("inventory-empty-search-history"),
    ).not.toBeInTheDocument();
  });

  it("shows the same generic empty state when the search box is empty", async () => {
    renderPage("/components");

    await waitFor(() => {
      expect(screen.getByTestId("inventory-empty")).toBeInTheDocument();
    });

    expect(
      screen.queryByTestId("inventory-empty-search-history"),
    ).not.toBeInTheDocument();
  });
});
