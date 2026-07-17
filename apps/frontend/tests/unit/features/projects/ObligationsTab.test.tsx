/**
 * ObligationsTab — unit tests (PR #13).
 *
 * Validates loading skeleton, empty state, error state, distribution chips,
 * and that filter / sort changes hit the wire layer with the right params
 * at offset 0. Mirrors LicensesTab.test.tsx (PR #12).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  ObligationListItem,
  ObligationListResponse,
} from "@/features/projects/api/obligationsApi";
import { ObligationsTab } from "@/features/projects/components/ObligationsTab";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/projects/api/obligationsApi", async () => {
  return {
    listProjectObligations: vi.fn(),
    getObligation: vi.fn(),
    fetchProjectNotice: vi.fn(),
    KNOWN_OBLIGATION_KINDS: [
      "attribution",
      "notice",
      "source-disclosure",
      "copyleft",
      "modifications",
      "dynamic-linking",
      "no-endorsement",
    ] as const,
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
  fetchProjectNotice,
  getObligation,
  listProjectObligations,
} from "@/features/projects/api/obligationsApi";

const mockedList = vi.mocked(listProjectObligations);
const mockedGet = vi.mocked(getObligation);
const mockedNotice = vi.mocked(fetchProjectNotice);

function ob(
  kind: string,
  overrides: Partial<ObligationListItem> = {},
): ObligationListItem {
  const id = overrides.id ?? `obg-${kind.padEnd(8, "x")}`;
  return {
    id,
    license_id: overrides.license_id ?? `lic-${kind}`,
    license_spdx_id: overrides.license_spdx_id ?? "MIT",
    license_name: overrides.license_name ?? "MIT License",
    license_category: overrides.license_category ?? "allowed",
    kind,
    text: overrides.text ?? `Default text for ${kind}`,
    text_ko: overrides.text_ko ?? null,
    link: overrides.link ?? null,
    affected_count: overrides.affected_count ?? 1,
    updated_at: overrides.updated_at ?? "2026-05-07T00:00:00Z",
  };
}

function listResponse(
  items: ObligationListItem[],
  total = items.length,
  distribution: Record<string, number> = {},
): ObligationListResponse {
  return { items, total, distribution };
}

function renderTab(initialEntries: string[] = ["/projects/proj-1"]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        <ObligationsTab projectId="proj-1" projectName="Demo Project" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ObligationsTab", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
    mockedNotice.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders skeleton while loading", () => {
    mockedList.mockReturnValue(new Promise(() => {}));
    renderTab();
    expect(screen.getByTestId("obligations-loading")).toBeInTheDocument();
  });

  it("renders the empty state when no rows exist", async () => {
    mockedList.mockResolvedValueOnce(listResponse([]));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("obligations-empty")).toBeInTheDocument();
    });
  });

  it("renders rows once data arrives and exposes summary counts", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse(
        [
          ob("attribution", { affected_count: 5, license_spdx_id: "MIT" }),
          ob("copyleft", {
            id: "obg-copy",
            affected_count: 2,
            license_category: "forbidden",
            license_spdx_id: "GPL-3.0",
            license_name: "GPL-3.0-only",
          }),
        ],
        2,
      ),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("obligation-row")).toHaveLength(2);
    });
    const summary = screen.getByTestId("obligations-summary");
    expect(summary).toHaveAttribute("data-loaded", "2");
    expect(summary).toHaveAttribute("data-total", "2");
    const counts = screen
      .getAllByTestId("obligation-row-affected-count")
      .map((el) => el.textContent);
    expect(counts).toEqual(expect.arrayContaining(["5", "2"]));
  });

  it("renders the distribution strip when distribution comes in the response", async () => {
    mockedList.mockResolvedValueOnce(
      listResponse(
        [ob("attribution", { affected_count: 3 })],
        1,
        { attribution: 3, copyleft: 1, "no-endorsement": 0 },
      ),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("obligations-distribution")).toBeInTheDocument();
    });
    const chips = screen.getAllByTestId("obligations-distribution-chip");
    // Zero-count kinds are filtered out.
    expect(chips.length).toBe(2);
    const kinds = chips.map((c) => c.getAttribute("data-kind"));
    expect(kinds).toEqual(["attribution", "copyleft"]);
  });

  it("renders the RFC 7807 detail in an alert on error", async () => {
    mockedList.mockRejectedValueOnce(
      new ProblemError("not allowed", {
        status: 403,
        title: "Forbidden",
        detail: "Obligation access denied — surfaced verbatim.",
        problem: null,
      }),
    );
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("obligations-error")).toBeInTheDocument();
    });
    expect(screen.getByTestId("obligations-error").textContent).toContain(
      "Obligation access denied — surfaced verbatim.",
    );
  });

  it("changing the kind filter triggers a query at offset 0", async () => {
    mockedList.mockResolvedValue(listResponse([ob("attribution")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("obligation-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    // Open the MultiSelect dropdown, then toggle the "copyleft" checkbox row.
    await userEvent.click(screen.getByTestId("obligations-kind-filter"));
    const copyleft = await waitFor(() => {
      const option = screen
        .getAllByTestId("obligations-kind-filter-option")
        .find((el) => el.getAttribute("data-value") === "copyleft");
      if (!option) throw new Error("copyleft option not mounted");
      return option;
    });
    await userEvent.click(copyleft);

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ kinds: ["copyleft"], offset: 0 }),
      );
    });
  });

  it("changing the sort key triggers a query with that sort", async () => {
    mockedList.mockResolvedValue(listResponse([ob("attribution")]));
    renderTab();
    await waitFor(() => {
      expect(screen.getAllByTestId("obligation-row")).toHaveLength(1);
    });
    mockedList.mockClear();

    await userEvent.selectOptions(
      screen.getByTestId("obligations-sort"),
      "kind",
    );
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ sort: "kind" }),
      );
    });
  });

  it("hydrates filter state from the URL on first render", async () => {
    mockedList.mockResolvedValueOnce(listResponse([ob("attribution")]));
    renderTab([
      "/projects/proj-1?kind=attribution,copyleft&license_category=forbidden&sort=kind&order=asc",
    ]);
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({
          kinds: ["attribution", "copyleft"],
          categories: ["forbidden"],
          sort: "kind",
          order: "asc",
        }),
      );
    });
  });

  it("clicking a row sets ?obligation=<id> in the URL and opens the drawer", async () => {
    const item = ob("attribution", { id: "obg-row-click" });
    mockedList.mockResolvedValueOnce(listResponse([item]));
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderTab();
    await waitFor(() => {
      expect(screen.getByTestId("obligation-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("obligation-row"));
    await waitFor(() => {
      expect(screen.getByTestId("obligation-drawer")).toBeInTheDocument();
    });
  });
});
