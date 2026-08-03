/**
 * AdminLayout — existence-hide guard tests.
 *
 * W4-A reduced AdminLayout to a guard wrapper: chrome (sidebar/header/logout)
 * is now owned by AppShell, so this layout only checks the super-admin bit
 * and renders either the outlet or the AdminNotFound page.
 *
 * We render under three actors:
 *   1. Super-admin → the outlet renders inside the guarded wrapper.
 *   2. Authenticated developer → AdminNotFound renders, guard hides the wrapper.
 *   3. No user (defensive) → AdminNotFound renders, guard hides the wrapper.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
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

  it("renders the outlet inside the guarded wrapper for a super-admin", () => {
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
    expect(screen.getByTestId("stub-outlet")).toHaveTextContent("stub-outlet");
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
});
