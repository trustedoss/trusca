/**
 * ApprovalsPage — unit tests (Phase 4 PR #15).
 *
 * Coverage targets:
 *   1. Renders rows once the list query resolves.
 *   2. Renders the empty state when the list is empty.
 *   3. Renders an error alert when the list query fails.
 *   4. Changing the status filter re-issues the list with the new status.
 *   5. Clicking a row opens the approvals drawer.
 *   6. Actions button click also opens the drawer.
 *   7. Next / Previous pagination controls change the page.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalsPage } from "@/features/approvals/ApprovalsPage";
import type { ApprovalListPage, ApprovalOut } from "@/lib/approvalsApi";

// ---------------------------------------------------------------------------
// Mock the API layer — keep tests free from network
// ---------------------------------------------------------------------------

vi.mock("@/lib/approvalsApi", async () => {
  return {
    listApprovals: vi.fn(),
    getApproval: vi.fn(),
    createApproval: vi.fn(),
    transitionApproval: vi.fn(),
    deleteApproval: vi.fn(),
  };
});

import { getApproval, listApprovals } from "@/lib/approvalsApi";

const mockedList = vi.mocked(listApprovals);
const mockedGet = vi.mocked(getApproval);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function approval(overrides: Partial<ApprovalOut> = {}): ApprovalOut {
  return {
    id: overrides.id ?? "aaaaaaaa-0000-0000-0000-000000000001",
    component_id:
      overrides.component_id ?? "comp-uuid-0000-0000-0000-000000000001",
    project_id:
      overrides.project_id ?? "proj-uuid-0000-0000-0000-000000000001",
    team_id: overrides.team_id ?? "team-uuid-0000-0000-0000-000000000001",
    requested_by_user_id: overrides.requested_by_user_id ?? "user-0001",
    requested_at: overrides.requested_at ?? "2026-05-01T10:00:00Z",
    status: overrides.status ?? "pending",
    decided_by_user_id: overrides.decided_by_user_id ?? null,
    decided_at: overrides.decided_at ?? null,
    decision_note: overrides.decision_note ?? null,
    version: overrides.version ?? 1,
    // Field-by-field above, so anything the list endpoint adds later is
    // dropped unless it is named. The spread carries the optional display
    // fields (component_name, project_name, requested_by_name) through
    // without each one needing its own line.
    ...overrides,
  };
}

function page(items: ApprovalOut[], total?: number): ApprovalListPage {
  return {
    items,
    total: total ?? items.length,
    page: 1,
    page_size: 25,
  };
}

function renderPage({ initialUrl = "/approvals" }: { initialUrl?: string } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[initialUrl]}>
      <QueryClientProvider client={client}>
        <ApprovalsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ApprovalsPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
  });

  it("renders rows once the list query resolves", async () => {
    mockedList.mockResolvedValue(
      page([
        approval({ id: "aaa00001", status: "pending" }),
        approval({ id: "aaa00002", status: "approved" }),
      ]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("approvals-row")).toHaveLength(2);
    });
    expect(screen.getByTestId("approvals-page")).toBeInTheDocument();
    expect(screen.getByTestId("approvals-table")).toBeInTheDocument();
  });

  it("renders the empty state when the list is empty", async () => {
    mockedList.mockResolvedValue(page([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-empty")).toBeInTheDocument();
    });
  });

  it("renders an error alert when the list query fails", async () => {
    mockedList.mockRejectedValue(new Error("server error"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-error")).toBeInTheDocument();
    });
  });

  it("changing the status filter re-issues the list with the new status", async () => {
    mockedList.mockResolvedValue(page([approval()]));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });

    const statusSelect = screen.getByTestId("approval-status-filter");
    await userEvent.selectOptions(statusSelect, "pending");

    await waitFor(() => {
      const lastCall = mockedList.mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ status: "pending" });
    });
  });

  it("hydrates the status filter + page from ?status= and ?page= deep-link (W12)", async () => {
    // W12 — filter URL persistence. The first query must already carry the
    // pre-applied filter AND page so reload / share lands the user on the
    // exact view they bookmarked.
    mockedList.mockResolvedValue(page([approval({ status: "pending" })]));
    renderPage({ initialUrl: "/approvals?status=pending&page=2" });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    const firstCall = mockedList.mock.calls[0]?.[0];
    expect(firstCall).toMatchObject({ status: "pending", page: 2 });
    // The <select> reflects the URL value too (so the chip / dropdown is in
    // sync with the data, not just the request).
    expect(
      (screen.getByTestId("approval-status-filter") as HTMLSelectElement).value,
    ).toBe("pending");
  });

  it("defaults to the open queue (pending + under_review) when ?status is absent (M-13)", async () => {
    mockedList.mockResolvedValue(page([approval({ status: "pending" })]));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    // The very first request must already carry the compound open filter —
    // disposed rows (approved / rejected) never flash into the default view.
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({
      status: "pending,under_review",
    });
    expect(
      (screen.getByTestId("approval-status-filter") as HTMLSelectElement).value,
    ).toBe("open");
  });

  it("explicit ?status=all deep-link requests every status (M-13)", async () => {
    mockedList.mockResolvedValue(page([approval({ status: "approved" })]));
    renderPage({ initialUrl: "/approvals?status=all" });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    // "all" is a UI sentinel — the request carries no status filter.
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({ status: null });
    expect(
      (screen.getByTestId("approval-status-filter") as HTMLSelectElement).value,
    ).toBe("all");
  });

  it("explicit ?status=approved deep-link narrows to that single status (M-13)", async () => {
    mockedList.mockResolvedValue(page([approval({ status: "approved" })]));
    renderPage({ initialUrl: "/approvals?status=approved" });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({ status: "approved" });
    expect(
      (screen.getByTestId("approval-status-filter") as HTMLSelectElement).value,
    ).toBe("approved");
  });

  it("switching the filter back to Open re-issues the compound open filter (M-13)", async () => {
    mockedList.mockResolvedValue(page([approval()]));
    renderPage({ initialUrl: "/approvals?status=all" });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });

    const statusSelect = screen.getByTestId("approval-status-filter");
    await userEvent.selectOptions(statusSelect, "open");

    await waitFor(() => {
      const lastCall = mockedList.mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ status: "pending,under_review" });
    });
  });

  it("clicking a row opens the approvals drawer", async () => {
    const a = approval({ id: "aaa-row-click-001" });
    mockedList.mockResolvedValue(page([a]));
    mockedGet.mockResolvedValue({ approval: a, etag: "1" });

    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("approvals-row")).toHaveLength(1);
    });

    const row = screen.getByTestId("approvals-row");
    await userEvent.click(row);

    // The drawer should mount and eventually load detail.
    await waitFor(() => {
      expect(screen.getByTestId("approvals-drawer")).toBeInTheDocument();
    });
  });

  it("clicking the Actions button opens the drawer without triggering row click twice", async () => {
    const a = approval({ id: "aaa-action-btn-001" });
    mockedList.mockResolvedValue(page([a]));
    mockedGet.mockResolvedValue({ approval: a, etag: "1" });

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-row-action")).toBeInTheDocument();
    });

    const actionBtn = screen.getByTestId("approvals-row-action");
    await userEvent.click(actionBtn);

    await waitFor(() => {
      expect(screen.getByTestId("approvals-drawer")).toBeInTheDocument();
    });
  });

  it("previous page button is disabled on page 1", async () => {
    mockedList.mockResolvedValue(page([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-page-prev")).toBeDisabled();
    });
  });

  it("closing the drawer via the Sheet close button resets drawer state", async () => {
    const a = approval({ id: "aaa-close-drawer" });
    mockedList.mockResolvedValue(page([a]));
    mockedGet.mockResolvedValue({ approval: a, etag: "1" });

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-row")).toBeInTheDocument();
    });

    // Open drawer by row click.
    await userEvent.click(screen.getByTestId("approvals-row"));
    await waitFor(() => {
      expect(screen.getByTestId("approvals-drawer")).toBeInTheDocument();
    });

    // Close via the Sheet's built-in close button.
    const closeBtn = screen.getByRole("button", { name: /close/i });
    await userEvent.click(closeBtn);

    // Once closed, the refresh button should remain on page (page itself intact).
    await waitFor(() => {
      expect(screen.getByTestId("approvals-refresh")).toBeInTheDocument();
    });
  });

  it("next page button increments the page counter", async () => {
    // 26 items so totalPages = 2
    mockedList.mockResolvedValue(
      page(Array.from({ length: 25 }, (_, i) => approval({ id: `id-${i}` })), 26),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("approvals-row")).toHaveLength(25);
    });

    const nextBtn = screen.getByTestId("approvals-page-next");
    expect(nextBtn).not.toBeDisabled();
    await userEvent.click(nextBtn);

    await waitFor(() => {
      const lastCall = mockedList.mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ page: 2 });
    });
  });
});

// ---------------------------------------------------------------------------
// B2 - the queue in the URL, the requester by name, an honest empty state
// ---------------------------------------------------------------------------

describe("ApprovalsPage (B2)", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
  });

  it("opens the drawer straight from the address", async () => {
    // Half a deep link is no deep link: arriving with ?approval= set has to
    // mount the drawer without a click, or a shared URL lands on the queue.
    const a = approval({ id: "aaaaaaaa-0000-0000-0000-0000000000aa" });
    mockedList.mockResolvedValue(page([a]));
    mockedGet.mockResolvedValue({ approval: a, etag: "1" });

    renderPage({ initialUrl: `/approvals?approval=${a.id}` });

    await waitFor(() => {
      expect(screen.getByTestId("approvals-drawer")).toBeInTheDocument();
    });
  });

  it("sends ?project= to the backend as a filter", async () => {
    const projectId = "bbbbbbbb-0000-0000-0000-000000000001";
    mockedList.mockResolvedValue(page([]));

    renderPage({ initialUrl: `/approvals?project=${projectId}` });

    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({
        project_id: projectId,
      });
    });
  });

  it("ignores a project id that is not a uuid rather than sending it", async () => {
    // The backend answers a malformed uuid query parameter with a 422, which
    // would take the whole queue down over one mistyped address.
    mockedList.mockResolvedValue(page([]));

    renderPage({ initialUrl: "/approvals?project=not-a-uuid" });

    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({
        project_id: null,
      });
    });
  });

  it("names the requester instead of printing part of a uuid", async () => {
    mockedList.mockResolvedValue(
      page([
        approval({
          id: "aaaaaaaa-0000-0000-0000-000000000011",
          requested_by_user_id: "cccccccc-0000-0000-0000-000000000001",
          requested_by_name: "Jin Park",
        }),
      ]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("approvals-row").textContent).toContain(
        "Jin Park",
      );
    });
    expect(screen.getByTestId("approvals-row").textContent).not.toContain(
      "cccccccc",
    );
  });

  it("does not claim a cause for an approval with no requester", async () => {
    // The row is real: the scan pipeline raises approvals with no user id.
    // The first wording said "Raised automatically", which is also what a
    // request would say if its requester's user row had been deleted, since
    // the 0008 migration sets the column NULL in that case. Naming a cause
    // the column cannot distinguish is the mistake G3 made with 409s.
    mockedList.mockResolvedValue(
      page([
        approval({
          id: "aaaaaaaa-0000-0000-0000-000000000012",
          requested_by_user_id: null,
          requested_by_name: null,
        }),
      ]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("approvals-row").textContent).toContain(
        "No requester recorded",
      );
    });
  });

  it("does not call a failed request an empty queue", async () => {
    // A query that did not answer is not a queue with nothing in it. The
    // page used to render the zero-state and a total of zero beside the
    // error banner, both stating a fact nobody had established.
    mockedList.mockRejectedValue(new Error("boom"));

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("approvals-error")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("approvals-empty")).toBeNull();
    expect(screen.queryByTestId("approvals-total")).toBeNull();
  });

  it("counts a page number as a filter, because it can empty the view", async () => {
    // Landing on page 3 of a queue that has since shrunk shows no rows, and
    // that is not the same thing as having none.
    mockedList.mockResolvedValue(page([]));

    renderPage({ initialUrl: "/approvals?page=3" });

    const empty = await screen.findByTestId("approvals-empty");
    expect(empty.textContent).toContain("No approvals match these filters");
    expect(screen.getByTestId("approvals-clear-filters")).toBeInTheDocument();
  });

  it("labels the project chip without falling back to a raw id", async () => {
    // The queue can be scoped to a project whose rows are all filtered out
    // by the status, and then no row carries the name.
    const projectId = "bbbbbbbb-0000-0000-0000-000000000002";
    mockedList.mockResolvedValue(page([]));

    renderPage({ initialUrl: `/approvals?project=${projectId}&status=approved` });

    const chip = await screen.findByTestId("approvals-project-filter");
    expect(chip.textContent).toContain("Selected project");
    expect(chip.textContent).not.toContain("bbbbbbbb");
  });

  it("matches the project id case-insensitively, as the database does", async () => {
    const lower = "bbbbbbbb-0000-0000-0000-000000000003";
    mockedList.mockResolvedValue(
      page([
        approval({
          id: "aaaaaaaa-0000-0000-0000-000000000014",
          project_id: lower,
          project_name: "payments-api",
        }),
      ]),
    );

    renderPage({ initialUrl: `/approvals?project=${lower.toUpperCase()}` });

    await waitFor(() => {
      expect(
        screen.getByTestId("approvals-project-filter").textContent,
      ).toContain("payments-api");
    });
  });

  it("does not blame filters for an empty queue when none are set", async () => {
    mockedList.mockResolvedValue(page([]));

    renderPage();

    const empty = await screen.findByTestId("approvals-empty");
    // The default view is the open queue, so the sentence has to be about
    // what is waiting, not about what was ever raised: a team that has
    // decided everything still has a history.
    expect(empty.textContent).toContain("Nothing is waiting for a decision");
    // Nothing to clear, so nothing offers to.
    expect(screen.queryByTestId("approvals-clear-filters")).toBeNull();
  });

  it("says filters are the reason when they are, and offers to drop them", async () => {
    mockedList.mockResolvedValue(page([]));

    renderPage({ initialUrl: "/approvals?status=approved" });

    const empty = await screen.findByTestId("approvals-empty");
    expect(empty.textContent).toContain("No approvals match these filters");

    await userEvent.click(screen.getByTestId("approvals-clear-filters"));

    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({
        status: "pending,under_review",
      });
    });
  });

  it("says how many requests there are, not just how many pages", async () => {
    mockedList.mockResolvedValue(
      page([approval({ id: "aaaaaaaa-0000-0000-0000-000000000013" })], 42),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("approvals-total").textContent).toContain("42");
    });
  });
});
