/**
 * AdminUsersPage — unit tests.
 *
 * We mock the wire layer so the page's behavior (loading skeleton, row
 * rendering, filter wiring, drawer open) is observable without a backend.
 *
 * Coverage targets:
 *   - Renders the toolbar + table + footer once data arrives.
 *   - Renders empty state when the list is empty.
 *   - Renders error alert on rejected query.
 *   - Toggles the role filter and re-issues the list query.
 *   - Clicking a row opens the user drawer (which itself fetches detail).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminUsersPage } from "@/features/admin/users/AdminUsersPage";
import type {
  AdminUserDetail,
  AdminUserListItem,
  AdminUserListResponse,
} from "@/features/admin/api/adminUsersApi";

vi.mock("@/features/admin/api/adminUsersApi", async () => {
  return {
    listAdminUsers: vi.fn(),
    getAdminUser: vi.fn(),
    updateUserRole: vi.fn(),
    deactivateUser: vi.fn(),
    activateUser: vi.fn(),
    requestPasswordReset: vi.fn(),
  };
});

import {
  getAdminUser,
  listAdminUsers,
} from "@/features/admin/api/adminUsersApi";

const mockedList = vi.mocked(listAdminUsers);
const mockedGet = vi.mocked(getAdminUser);

function user(
  email: string,
  overrides: Partial<AdminUserListItem> = {},
): AdminUserListItem {
  return {
    id: overrides.id ?? `id-${email}`,
    email,
    full_name: overrides.full_name ?? "Test User",
    is_active: overrides.is_active ?? true,
    is_superuser: overrides.is_superuser ?? false,
    last_login_at: overrides.last_login_at ?? "2026-05-05T00:00:00Z",
    created_at: overrides.created_at ?? "2026-04-01T00:00:00Z",
  };
}

function listResponse(items: AdminUserListItem[]): AdminUserListResponse {
  return { items, total: items.length, page: 1, page_size: 50 };
}

function detail(item: AdminUserListItem): AdminUserDetail {
  return {
    ...item,
    updated_at: item.created_at,
    scan_count: 3,
    memberships: [],
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminUsersPage />
    </QueryClientProvider>,
  );
}

describe("AdminUsersPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders rows once the list query resolves", async () => {
    mockedList.mockResolvedValue(
      listResponse([user("alice@example.com"), user("bob@example.com")]),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-users-row")).toHaveLength(2);
    });
    expect(screen.getByTestId("admin-users-page")).toBeInTheDocument();
  });

  it("renders the empty state when the list is empty", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-users-empty")).toBeInTheDocument();
    });
  });

  it("renders an error alert when the list query fails", async () => {
    mockedList.mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-users-error")).toBeInTheDocument();
    });
  });

  it("changing the role filter re-issues the list with a role query", async () => {
    mockedList.mockResolvedValue(listResponse([user("alice@example.com")]));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    const roleSelect = screen.getByTestId("admin-users-role-filter");
    await userEvent.selectOptions(roleSelect, "team_admin");
    await waitFor(() => {
      // Second call carries the role param.
      const lastCall = mockedList.mock.calls.at(-1)?.[0];
      expect(lastCall).toMatchObject({ role: "team_admin" });
    });
  });

  it("debounces the search input before re-querying", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedList.mockResolvedValue(
      listResponse([user("alice@example.com"), user("bob@example.com")]),
    );
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    const search = screen.getByTestId("admin-users-search");
    await userEvent.type(search, "alpha");
    // Before the debounce window, no extra fetch.
    expect(mockedList).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last?.search).toBe("alpha");
    });
  });

  it("opens the user drawer on row click and fetches detail", async () => {
    const alice = user("alice@example.com");
    mockedList.mockResolvedValue(listResponse([alice]));
    mockedGet.mockResolvedValue(detail(alice));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-users-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-users-row"));
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith(alice.id);
    });
    // Drawer rendered once detail resolves.
    await waitFor(() => {
      expect(screen.getByTestId("admin-user-drawer")).toBeInTheDocument();
    });
  });

  it("toggles the active filter and re-issues the list with active=true|false", async () => {
    mockedList.mockResolvedValue(listResponse([user("alice@example.com")]));
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    const activeSelect = screen.getByTestId("admin-users-active-filter");
    await userEvent.selectOptions(activeSelect, "active");
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ active: true });
    });
    await userEvent.selectOptions(activeSelect, "inactive");
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ active: false });
    });
  });

  it("changing the page size resets to page 1 and re-queries", async () => {
    // Two rows (so a page-size change is observable) — the data shape itself
    // doesn't matter, we're exercising the inline `onChange` handler.
    mockedList.mockResolvedValue(
      listResponse([user("alice@example.com"), user("bob@example.com")]),
    );
    renderPage();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledTimes(1);
    });
    const pageSize = screen.getByTestId("admin-users-page-size");
    await userEvent.selectOptions(pageSize, "100");
    await waitFor(() => {
      const last = mockedList.mock.calls.at(-1)?.[0];
      expect(last).toMatchObject({ page_size: 100, page: 1 });
    });
  });

  it("renders pagination buttons that disable at the page bounds", async () => {
    // Single page of results — both prev and next should be disabled.
    mockedList.mockResolvedValue(listResponse([user("alice@example.com")]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-users-page-prev")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-users-page-prev")).toBeDisabled();
    expect(screen.getByTestId("admin-users-page-next")).toBeDisabled();
  });
});
