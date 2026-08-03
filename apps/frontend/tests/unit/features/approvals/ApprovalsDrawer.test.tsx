/**
 * ApprovalsDrawer — unit tests (Phase 4 PR #15).
 *
 * Coverage targets:
 *   1. Renders skeleton while loading.
 *   2. Renders approval details once fetched.
 *   3. Shows Start Review + Reject buttons for "pending" status.
 *   4. Shows Approve + Reject buttons for "under_review" status.
 *   5. Shows no action buttons for "approved" / "rejected" status.
 *   6. Clicking Start Review shows confirm strip, confirming calls transition.
 *   7. Hides action buttons when user is not super_admin or team_admin.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalsDrawer } from "@/features/approvals/ApprovalsDrawer";
import type { ApprovalOut } from "@/lib/approvalsApi";
import { useAuthStore } from "@/stores/authStore";

// ---------------------------------------------------------------------------
// Mocks
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

import {
  getApproval,
  transitionApproval,
} from "@/lib/approvalsApi";

const mockedGet = vi.mocked(getApproval);
const mockedTransition = vi.mocked(transitionApproval);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function approval(overrides: Partial<ApprovalOut> = {}): ApprovalOut {
  return {
    id: "bbbbbbbb-0000-0000-0000-000000000001",
    component_id: "comp-aaaaaa-0000-0000-0000-000000000001",
    project_id: "proj-aaaaaa-0000-0000-0000-000000000001",
    team_id: "team-aaaaaa-0000-0000-0000-000000000001",
    requested_by_user_id: "user-0001",
    requested_at: "2026-05-01T10:00:00Z",
    status: "pending",
    decided_by_user_id: null,
    decided_at: null,
    decision_note: null,
    version: 1,
    ...overrides,
  };
}

function setUser(role: "super_admin" | "team_admin" | "developer") {
  useAuthStore.setState({
    user: {
      id: "test-user",
      email: "test@example.com",
      displayName: "Test",
      role,
      isActive: true,
      isSuperuser: role === "super_admin",
      teamId: null,
    },
    status: "authenticated",
    isAuthenticated: true,
    accessToken: "test-token",
  });
}

interface DrawerProps {
  approvalId?: string | null;
  open?: boolean;
}

function renderDrawer({ approvalId = "bbbbbbbb-0000-0000-0000-000000000001", open = true }: DrawerProps = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const notify = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <ApprovalsDrawer
        open={open}
        approvalId={approvalId}
        onOpenChange={vi.fn()}
        notify={notify}
      />
    </QueryClientProvider>,
  );
  return { notify };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("ApprovalsDrawer", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedTransition.mockReset();
    setUser("super_admin");
  });

  it("renders a loading skeleton while the detail query is in-flight", () => {
    // Never resolve so it stays loading.
    mockedGet.mockReturnValue(new Promise(() => {}));
    renderDrawer();
    expect(screen.getByTestId("approvals-drawer-loading")).toBeInTheDocument();
  });

  it("renders approval detail once the query resolves", async () => {
    const a = approval();
    mockedGet.mockResolvedValue({ approval: a, etag: "1" });
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-drawer")).toBeInTheDocument();
      expect(screen.getByTestId("approval-status-badge")).toBeInTheDocument();
    });
    // Component ID should appear somewhere in the drawer (SheetDescription + Meta).
    expect(screen.getAllByText(a.component_id).length).toBeGreaterThanOrEqual(1);
  });

  it("shows Start Review and Reject buttons for pending approval", async () => {
    mockedGet.mockResolvedValue({ approval: approval({ status: "pending" }), etag: "1" });
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-action-start-review")).toBeInTheDocument();
      expect(screen.getByTestId("approvals-action-reject")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("approvals-action-approve"),
    ).not.toBeInTheDocument();
  });

  it("shows Approve and Reject buttons for under_review approval", async () => {
    mockedGet.mockResolvedValue({
      approval: approval({ status: "under_review" }),
      etag: "2",
    });
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-action-approve")).toBeInTheDocument();
      expect(screen.getByTestId("approvals-action-reject")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("approvals-action-start-review"),
    ).not.toBeInTheDocument();
  });

  it("shows no action buttons for approved status", async () => {
    mockedGet.mockResolvedValue({
      approval: approval({ status: "approved" }),
      etag: "3",
    });
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approval-status-badge")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("approvals-drawer-actions"),
    ).not.toBeInTheDocument();
  });

  it("shows no action buttons for rejected status", async () => {
    mockedGet.mockResolvedValue({
      approval: approval({ status: "rejected" }),
      etag: "3",
    });
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approval-status-badge")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("approvals-drawer-actions"),
    ).not.toBeInTheDocument();
  });

  it("clicking Start Review shows confirm strip, confirming calls transition API", async () => {
    const a = approval({ status: "pending" });
    mockedGet.mockResolvedValue({ approval: a, etag: "1" });
    mockedTransition.mockResolvedValue({ ...a, status: "under_review", version: 2 });

    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-action-start-review")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("approvals-action-start-review"));

    // Confirm strip should appear.
    await waitFor(() => {
      expect(screen.getByTestId("approvals-confirm-strip")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("approvals-confirm-ok"));

    await waitFor(() => {
      expect(mockedTransition).toHaveBeenCalledWith(
        a.id,
        "under_review",
        "1",
        undefined,
      );
    });
  });

  it("hides action buttons when the user is a plain developer", async () => {
    setUser("developer");
    mockedGet.mockResolvedValue({ approval: approval({ status: "pending" }), etag: "1" });
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approval-status-badge")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("approvals-drawer-actions"),
    ).not.toBeInTheDocument();
  });

  it("confirm cancel hides the confirm strip without calling transition", async () => {
    const a = approval({ status: "pending" });
    mockedGet.mockResolvedValue({ approval: a, etag: "1" });

    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("approvals-action-start-review")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("approvals-action-start-review"));
    await waitFor(() => {
      expect(screen.getByTestId("approvals-confirm-strip")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("approvals-confirm-cancel"));
    await waitFor(() => {
      expect(
        screen.queryByTestId("approvals-confirm-strip"),
      ).not.toBeInTheDocument();
    });
    expect(mockedTransition).not.toHaveBeenCalled();
  });
});
