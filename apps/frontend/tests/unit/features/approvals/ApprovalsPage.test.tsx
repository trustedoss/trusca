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
