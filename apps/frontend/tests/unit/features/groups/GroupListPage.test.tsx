/**
 * GroupListPage — unit tests, group-hierarchy Phase 4 PR 4-B.
 *
 * Covers: root rows render, drill-into a child level re-queries by
 * `parent_id` and grows the trail, the trail's root control clears it back,
 * search switches to a flat `q` query, and "View details" leaves the list
 * for the group's own detail route (two separate row affordances, per the
 * GroupsHarness contract PR 4-A pinned).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GroupListItem, GroupListPage as GroupListPageEnvelope } from "@/features/groups/api/groupsApi";

vi.mock("@/features/groups/api/groupsApi", () => ({
  listGroups: vi.fn(),
}));

import { listGroups } from "@/features/groups/api/groupsApi";
import { GroupListPage } from "@/features/groups/GroupListPage";

const mockedList = vi.mocked(listGroups);

function group(name: string, overrides: Partial<GroupListItem> = {}): GroupListItem {
  return {
    id: overrides.id ?? `id-${name}`,
    name,
    slug: overrides.slug ?? name.toLowerCase(),
    description: overrides.description ?? null,
    parent_group_id: overrides.parent_group_id ?? null,
    child_group_count: overrides.child_group_count ?? 0,
    project_count: overrides.project_count ?? 0,
    member_count: overrides.member_count ?? 0,
    updated_at: overrides.updated_at ?? "2026-05-01T00:00:00Z",
  };
}

function page(items: GroupListItem[]): GroupListPageEnvelope {
  return { items, total: items.length, page: 1, page_size: 50 };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/groups"]}>
        <Routes>
          <Route path="/groups" element={<GroupListPage />} />
          <Route
            path="/groups/:groupId"
            element={<div data-testid="group-detail-stub" />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GroupListPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders root rows with their badge counts", async () => {
    mockedList.mockResolvedValueOnce(
      page([
        group("Platform", { child_group_count: 2, project_count: 3, member_count: 5 }),
        group("Payments"),
      ]),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("groups-row")).toHaveLength(2);
    });
    const platformRow = screen
      .getAllByTestId("groups-row")
      .find((row) => row.getAttribute("data-group-name") === "Platform")!;
    expect(
      platformRow.querySelector('[data-testid="groups-row-child-count"]'),
    ).toHaveAttribute("data-value", "2");
    expect(mockedList).toHaveBeenCalledWith({
      q: undefined,
      parent_id: undefined,
      page: 1,
      page_size: 50,
    });
  });

  it("drilling into a row re-queries by parent_id and extends the trail", async () => {
    mockedList.mockResolvedValueOnce(
      page([group("Platform", { id: "platform-id", child_group_count: 1 })]),
    );
    mockedList.mockResolvedValueOnce(page([group("Backend", { id: "backend-id" })]));
    renderPage();

    await waitFor(() => {
      expect(screen.getAllByTestId("groups-row")).toHaveLength(1);
    });

    await userEvent.click(screen.getByTestId("groups-row-name"));

    await waitFor(() => {
      expect(mockedList).toHaveBeenLastCalledWith({
        q: undefined,
        parent_id: "platform-id",
        page: 1,
        page_size: 50,
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("groups-drilldown-breadcrumb-segment")).toHaveAttribute(
        "data-group-name",
        "Platform",
      );
    });

    // The root control clears the trail and re-queries the root level.
    mockedList.mockResolvedValueOnce(
      page([group("Platform", { id: "platform-id" })]),
    );
    await userEvent.click(screen.getByTestId("groups-drilldown-breadcrumb-root"));
    await waitFor(() => {
      expect(
        screen.queryByTestId("groups-drilldown-breadcrumb-segment"),
      ).not.toBeInTheDocument();
    });
  });

  it("typing a search term switches to a flat q query", async () => {
    mockedList.mockResolvedValueOnce(page([group("Platform")]));
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("groups-row")).toHaveLength(1);
    });

    mockedList.mockResolvedValueOnce(page([group("Payments Core")]));
    await userEvent.type(screen.getByTestId("groups-search"), "pay");

    await waitFor(
      () => {
        expect(mockedList).toHaveBeenLastCalledWith({
          q: "pay",
          parent_id: undefined,
          page: 1,
          page_size: 50,
        });
      },
      { timeout: 2000 },
    );
  });

  it("opening detail from a row leaves the list for the group's own route", async () => {
    mockedList.mockResolvedValueOnce(page([group("Platform", { id: "platform-id" })]));
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("groups-row")).toHaveLength(1);
    });

    await userEvent.click(screen.getByTestId("groups-row-open-detail"));

    await waitFor(() => {
      expect(screen.getByTestId("group-detail-stub")).toBeInTheDocument();
    });
  });

  it("shows the empty state when a search matches nothing", async () => {
    mockedList.mockResolvedValueOnce(page([group("Platform")]));
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("groups-row")).toHaveLength(1);
    });

    mockedList.mockResolvedValueOnce(page([]));
    await userEvent.type(screen.getByTestId("groups-search"), "nonexistent");

    await waitFor(
      () => {
        expect(screen.getByTestId("groups-empty")).toBeInTheDocument();
      },
      { timeout: 2000 },
    );
  });
});
