/**
 * AdminLayout — existence-hide tests.
 *
 * Renders the layout under three actors:
 *   1. Super-admin → the layout chrome and the matching outlet text appear.
 *   2. Authenticated developer → the AdminNotFound page renders instead.
 *   3. No user (defensive) → the AdminNotFound page renders instead.
 *
 * We use MemoryRouter and a tiny stub child route so the layout's <Outlet />
 * has something to render.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AdminLayout } from "@/features/admin/AdminLayout";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

function setUser(user: AuthUser | null) {
  useAuthStore.setState({
    user,
    accessToken: "tok",
    status: "authenticated",
    isAuthenticated: true,
  });
}

function renderLayout() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/admin/users"]}>
        <Routes>
          <Route path="/admin" element={<AdminLayout />}>
            <Route
              path="users"
              element={<div data-testid="stub-outlet">stub-outlet</div>}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AdminLayout", () => {
  beforeEach(() => {
    setUser(null);
  });
  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("renders the chrome and outlet for a super-admin", () => {
    setUser({
      id: "u-super",
      email: "super@example.com",
      displayName: "Super",
      role: "super_admin",
      isActive: true,
      isSuperuser: true,
      teamId: null,
    });
    renderLayout();
    expect(screen.getByTestId("admin-layout")).toBeInTheDocument();
    expect(screen.getByTestId("admin-sidebar")).toBeInTheDocument();
    expect(screen.getByTestId("stub-outlet")).toHaveTextContent("stub-outlet");
    // Sidebar nav links present.
    expect(screen.getByTestId("admin-nav-users")).toBeInTheDocument();
    expect(screen.getByTestId("admin-nav-teams")).toBeInTheDocument();
  });

  it("hides the layout (renders 404) for a non-super-admin", () => {
    setUser({
      id: "u-dev",
      email: "dev@example.com",
      displayName: "Dev",
      role: "developer",
      isActive: true,
      isSuperuser: false,
      teamId: null,
    });
    renderLayout();
    expect(screen.queryByTestId("admin-layout")).not.toBeInTheDocument();
    expect(screen.getByTestId("admin-not-found")).toBeInTheDocument();
  });

  it("renders 404 when no user is loaded yet", () => {
    setUser(null);
    renderLayout();
    expect(screen.queryByTestId("admin-layout")).not.toBeInTheDocument();
    expect(screen.getByTestId("admin-not-found")).toBeInTheDocument();
  });

  it("invokes auth.logout when the sign-out button is clicked", async () => {
    setUser({
      id: "u-super",
      email: "super@example.com",
      displayName: "Super",
      role: "super_admin",
      isActive: true,
      isSuperuser: true,
      teamId: null,
    });
    const logoutSpy = vi.fn(async () => {});
    // Replace the store's logout with the spy so the component invokes it.
    useAuthStore.setState({ logout: logoutSpy });

    renderLayout();
    await userEvent.click(screen.getByTestId("admin-logout"));

    await waitFor(() => {
      expect(logoutSpy).toHaveBeenCalledTimes(1);
    });
  });
});
