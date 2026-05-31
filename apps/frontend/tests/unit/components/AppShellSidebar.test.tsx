import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { AppProviders } from "@/components/AppProviders";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

// AppShell mounts the full authenticated app; mock the network-touching
// modules the landing routes use so the shell renders without HTTP. Mirrors
// the setup in App.test.tsx.
vi.mock("@/lib/api", () => ({
  fetchMe: vi.fn(),
  postLogin: vi.fn(),
  postRegister: vi.fn(),
  postLogout: vi.fn(),
}));

vi.mock("@/lib/projectsApi", () => ({
  listProjects: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, size: 100 }),
  listMyScans: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, size: 10 }),
  triggerScan: vi.fn(),
}));

vi.mock("@/lib/approvalsApi", () => ({
  listApprovals: vi
    .fn()
    .mockResolvedValue({ items: [], total: 0, page: 1, page_size: 1 }),
}));

const fakeUser: AuthUser = {
  id: "u-1",
  email: "alice@example.com",
  displayName: "Alice",
  role: "developer",
  isActive: true,
  isSuperuser: false,
  teamId: null,
  teams: [],
};

function renderAppAt(path: string) {
  window.history.replaceState(null, "", path);
  return render(
    <AppProviders>
      <App />
    </AppProviders>,
  );
}

describe("AppShell — collapsible sidebar", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useUIStore.setState({ sidebarCollapsed: false });
    useAuthStore.setState({
      user: fakeUser,
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    useAuthStore.getState().reset();
    useUIStore.setState({ sidebarCollapsed: false });
    window.history.replaceState(null, "", "/");
  });

  it("starts expanded with visible nav labels", async () => {
    renderAppAt("/projects");
    const sidebar = await screen.findByTestId("app-sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "false");
    // Expanded → the link carries its visible text label.
    expect(screen.getByTestId("nav-projects")).toHaveTextContent("Projects");
  });

  it("collapsing hides labels, flips data-collapsed, and persists", async () => {
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-sidebar");

    await user.click(screen.getByTestId("sidebar-collapse-toggle"));

    const sidebar = screen.getByTestId("app-sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "true");

    // Collapsed → no visible text, accessible name preserved via aria-label.
    const projects = screen.getByTestId("nav-projects");
    expect(projects).toHaveTextContent("");
    expect(projects).toHaveAttribute("aria-label", "Projects");

    // Persisted so the next visit stays collapsed.
    expect(
      JSON.parse(window.localStorage.getItem("trustedoss-ui") as string).state
        .sidebarCollapsed,
    ).toBe(true);
  });

  it("re-renders collapsed when the store was already collapsed", async () => {
    useUIStore.setState({ sidebarCollapsed: true });
    renderAppAt("/projects");
    const sidebar = await screen.findByTestId("app-sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "true");
  });

  it("opens the mobile drawer and closes it on navigate", async () => {
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-sidebar");

    // Drawer is not mounted until the hamburger is pressed.
    expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("sidebar-mobile-trigger"));
    const drawer = await screen.findByTestId("mobile-nav-drawer");

    // The drawer carries its own copy of the nav with full labels.
    const drawerProjects = within(drawer).getByTestId("nav-projects");
    expect(drawerProjects).toHaveTextContent("Projects");

    // Clicking a nav item closes the drawer (onNavigate).
    await user.click(drawerProjects);
    await waitFor(() => {
      expect(screen.queryByTestId("mobile-nav-drawer")).not.toBeInTheDocument();
    });
  });
});
