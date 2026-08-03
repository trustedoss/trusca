// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * InventoryPage — unit tests for the empty state's way out.
 *
 * The inventory lists what each project's LATEST successful scan found. The
 * search page reaches back through every scan a project has run. When a term
 * finds nothing here it may still be in that history, and until now nothing on
 * screen said so — the two surfaces share tab names ("Components") and looked
 * like one was simply broken.
 *
 * The offer is conditional on purpose: with an empty search box there is no
 * term to carry across, so the generic "scan a project" copy stands.
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
  it("offers the scan history when a term found nothing", async () => {
    renderPage("/components?inv_search=lodash");

    await waitFor(() => {
      expect(screen.getByTestId("inventory-empty")).toBeInTheDocument();
    });

    const link = await screen.findByTestId("inventory-empty-search-history");
    // The term rides along, and the components tab is the one that answers the
    // same question the user just asked here.
    expect(link).toHaveAttribute(
      "href",
      "/search?kind=components&q=lodash",
    );
  });

  it("stays generic when the search box is empty", async () => {
    renderPage("/components");

    await waitFor(() => {
      expect(screen.getByTestId("inventory-empty")).toBeInTheDocument();
    });

    // Nothing to carry across — a tenant that has never scanned should be told
    // to scan, not sent to search a history that does not exist.
    expect(
      screen.queryByTestId("inventory-empty-search-history"),
    ).not.toBeInTheDocument();
  });

  it("percent-encodes a term before putting it in the link", async () => {
    renderPage("/components?inv_search=%40scope%2Fpkg");

    const link = await screen.findByTestId("inventory-empty-search-history");
    expect(link).toHaveAttribute(
      "href",
      "/search?kind=components&q=%40scope%2Fpkg",
    );
  });
});
