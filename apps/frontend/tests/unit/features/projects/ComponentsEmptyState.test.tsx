/**
 * The components table's two empty states (C3).
 *
 * Which of the two renders is a branch, and a branch with no test is how the
 * defect this unit exists to remove came back: the filter copy showing on a
 * project where no filter is set. Asserting the strings in the locale file
 * proves they exist and says nothing about which one a reader sees.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComponentsTab } from "@/features/projects/components/ComponentsTab";

const listComponents = vi.fn();

vi.mock("@/features/projects/api/useComponents", () => ({
  useComponents: () => listComponents(),
}));

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";

/** An answer with no rows, however the reader got there. */
function noRows() {
  return {
    data: { pages: [{ items: [], total: 0 }] },
    isLoading: false,
    isError: false,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  };
}

function renderTab({
  entry = `/projects/${PROJECT_ID}`,
  onScan,
}: { entry?: string; onScan?: () => void } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <ComponentsTab projectId={PROJECT_ID} onScan={onScan} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ComponentsTab empty states", () => {
  beforeEach(() => {
    listComponents.mockReset();
    listComponents.mockReturnValue(noRows());
  });
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("does not blame a filter on a project with no filter set", async () => {
    const onScan = vi.fn();

    renderTab({ onScan });

    const empty = await screen.findByTestId("components-empty");
    expect(empty.textContent).not.toContain("filter");
    // A scan is what fills this table, and it is the only thing that will.
    await userEvent.click(screen.getByTestId("components-empty-scan"));
    expect(onScan).toHaveBeenCalledOnce();
  });

  it("does blame the filter when one is set", async () => {
    const onScan = vi.fn();

    renderTab({ entry: `/projects/${PROJECT_ID}?severity=critical`, onScan });

    const empty = await screen.findByTestId("components-empty");
    expect(empty.textContent).toContain("filters");
    // And offers no scan: the rows exist, the filter is hiding them, and a
    // scan would not change that.
    expect(screen.queryByTestId("components-empty-scan")).toBeNull();
  });

  it("counts a search term as a filter", async () => {
    // The narrowing check lists every toolbar control. A term left in the
    // search box is the easiest one to forget and the easiest to leave set.
    renderTab({ entry: `/projects/${PROJECT_ID}?components_search=nothing-matches`, onScan: vi.fn() });

    const empty = await screen.findByTestId("components-empty");
    expect(empty.textContent).toContain("filters");
  });

  it("offers no scan when the reader cannot start one", async () => {
    renderTab();

    await screen.findByTestId("components-empty");
    expect(screen.queryByTestId("components-empty-scan")).toBeNull();
  });
});
