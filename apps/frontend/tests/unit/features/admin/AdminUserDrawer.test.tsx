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
  };
});

import {
  deactivateUser,
  getAdminUser,
  requestPasswordReset,
  updateUserRole,
} from "@/features/admin/api/adminUsersApi";

const mockedGet = vi.mocked(getAdminUser);
const mockedUpdateRole = vi.mocked(updateUserRole);
const mockedDeactivate = vi.mocked(deactivateUser);
const mockedReset = vi.mocked(requestPasswordReset);

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
    expect(notify).toHaveBeenCalledWith(expect.any(String), "success");
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
    expect(notify).toHaveBeenCalledWith(expect.any(String), "success");
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
    expect(notify).toHaveBeenCalledWith(expect.any(String), "success");
  });
});
