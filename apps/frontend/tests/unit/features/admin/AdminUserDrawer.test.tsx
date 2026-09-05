/**
 * AdminUserDrawer — unit tests.
 *
 * Cover the role-form, deactivate-confirm, and password-reset flows. Each
 * test exercises a single mutation; we mock the wire surface so the drawer's
 * behavior is observable without a backend.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AdminUserDetail,
  RoleUpdatePayload,
} from "@/features/admin/api/adminUsersApi";
import { AdminUserDrawer } from "@/features/admin/users/AdminUserDrawer";

vi.mock("@/features/admin/api/adminUsersApi", async () => {
  return {
    getAdminUser: vi.fn(),
    updateUserRole: vi.fn(),
    deactivateUser: vi.fn(),
    activateUser: vi.fn(),
    requestPasswordReset: vi.fn(),
    unlockSignIn: vi.fn(),
    clearMfa: vi.fn(),
  };
});

import {
  clearMfa,
  deactivateUser,
  getAdminUser,
  requestPasswordReset,
  unlockSignIn,
  updateUserRole,
} from "@/features/admin/api/adminUsersApi";

const mockedGet = vi.mocked(getAdminUser);
const mockedUpdateRole = vi.mocked(updateUserRole);
const mockedDeactivate = vi.mocked(deactivateUser);
const mockedReset = vi.mocked(requestPasswordReset);
const mockedUnlock = vi.mocked(unlockSignIn);
const mockedClearMfa = vi.mocked(clearMfa);

function detail(overrides: Partial<AdminUserDetail> = {}): AdminUserDetail {
  return {
    id: "u1",
    email: "alice@example.com",
    full_name: "Alice",
    is_active: true,
    is_superuser: false,
    last_login_at: "2026-05-01T00:00:00Z",
    created_at: "2026-04-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    scan_count: 5,
    mfa_enabled: false,
    memberships: [],
    ...overrides,
  };
}

function renderDrawer(notify = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    notify,
    ...render(
      <QueryClientProvider client={client}>
        <AdminUserDrawer
          open
          userId="u1"
          onOpenChange={() => {}}
          notify={notify}
        />
      </QueryClientProvider>,
    ),
  };
}

describe("AdminUserDrawer", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedUpdateRole.mockReset();
    mockedDeactivate.mockReset();
    mockedReset.mockReset();
    mockedUnlock.mockReset();
    mockedClearMfa.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the user detail once it loads", async () => {
    mockedGet.mockResolvedValue(detail());
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    expect(screen.getByText("alice@example.com")).toBeInTheDocument();
  });

  it("opens the role-change form and saves a new role", async () => {
    mockedGet.mockResolvedValue(detail());
    mockedUpdateRole.mockResolvedValue(detail({ is_superuser: true }));
    const { notify } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-user-action-change-role"));
    expect(screen.getByTestId("admin-user-role-form")).toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByTestId("admin-user-role-select"),
      "super_admin",
    );
    await userEvent.click(screen.getByTestId("admin-user-role-save"));
    await waitFor(() => {
      expect(mockedUpdateRole).toHaveBeenCalledTimes(1);
    });
    const args = mockedUpdateRole.mock.calls[0];
    const payload = args[1] as RoleUpdatePayload;
    expect(args[0]).toBe("u1");
    expect(payload.role).toBe("super_admin");
    expect(notify).toHaveBeenCalledWith(
      expect.any(String),
      "success",
      expect.any(String),
    );
  });

  it("requires confirmation before deactivating, then dispatches the mutation", async () => {
    mockedGet.mockResolvedValue(detail());
    mockedDeactivate.mockResolvedValue(detail({ is_active: false }));
    const { notify } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-user-action-deactivate"));
    // The confirmation strip appears before the mutation fires.
    expect(screen.getByTestId("admin-user-confirm-strip")).toBeInTheDocument();
    expect(mockedDeactivate).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("admin-user-confirm-ok"));
    await waitFor(() => {
      expect(mockedDeactivate).toHaveBeenCalledWith("u1");
    });
    expect(notify).toHaveBeenCalledWith(
      expect.any(String),
      "success",
      expect.any(String),
    );
  });

  it("emits a success notification on password reset", async () => {
    mockedGet.mockResolvedValue(detail());
    mockedReset.mockResolvedValue();
    const { notify } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-user-action-reset"));
    await userEvent.click(screen.getByTestId("admin-user-confirm-ok"));
    await waitFor(() => {
      expect(mockedReset).toHaveBeenCalledWith("u1");
    });
    expect(notify).toHaveBeenCalledWith(
      expect.any(String),
      "success",
      expect.any(String),
    );
  });

  it("unlocks sign-in for an address that failed too often", async () => {
    mockedGet.mockResolvedValue(detail());
    mockedUnlock.mockResolvedValue();
    const { notify } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-user-action-unlock"));
    await userEvent.click(screen.getByTestId("admin-user-confirm-ok"));
    await waitFor(() => {
      expect(mockedUnlock).toHaveBeenCalledWith("u1");
    });
    expect(notify).toHaveBeenCalledWith(
      expect.any(String),
      "success",
      "sign_in_unlocked",
    );
    // The other actions share the confirm strip. If the dispatch fell back to
    // a neighbour, this is what would show it.
    expect(mockedReset).not.toHaveBeenCalled();
    expect(mockedClearMfa).not.toHaveBeenCalled();
  });

  it("offers to clear a second factor only when there is one", async () => {
    mockedGet.mockResolvedValue(detail({ mfa_enabled: false }));
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("admin-user-action-clear-mfa"),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("admin-user-mfa-state")).toHaveTextContent(
      "Not set up",
    );
  });

  it("clears the second factor after confirmation", async () => {
    mockedGet.mockResolvedValue(detail({ mfa_enabled: true }));
    mockedClearMfa.mockResolvedValue();
    const { notify } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-user-mfa-state")).toHaveTextContent(
      "Required",
    );
    await userEvent.click(screen.getByTestId("admin-user-action-clear-mfa"));
    // The wording has to say what the person loses, because they cannot get
    // it back from here: the codes are gone and the sessions are closed.
    expect(screen.getByTestId("admin-user-confirm-strip")).toHaveTextContent(
      /recovery codes/i,
    );
    await userEvent.click(screen.getByTestId("admin-user-confirm-ok"));
    await waitFor(() => {
      expect(mockedClearMfa).toHaveBeenCalledWith("u1");
    });
    expect(notify).toHaveBeenCalledWith(
      expect.any(String),
      "success",
      "mfa_cleared",
    );
    expect(mockedUnlock).not.toHaveBeenCalled();
  });

  it("reports a failure to clear rather than claiming it worked", async () => {
    mockedGet.mockResolvedValue(detail({ mfa_enabled: true }));
    mockedClearMfa.mockRejectedValue(new Error("boom"));
    const { notify } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-user-action-clear-mfa"));
    await userEvent.click(screen.getByTestId("admin-user-confirm-ok"));
    await waitFor(() => {
      expect(notify).toHaveBeenCalledWith(
        expect.any(String),
        "error",
        "unknown",
      );
    });
  });
});
