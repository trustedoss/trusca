import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { AppProviders } from "@/components/AppProviders";
import { useAuthStore, type AuthUser } from "@/stores/authStore";

vi.mock("@/lib/api", () => ({
  fetchMe: vi.fn(),
  postLogin: vi.fn(),
  postRegister: vi.fn(),
  postLogout: vi.fn(),
}));

// ProjectListPage calls listProjects — mock it so the test doesn't hit the
// network and so the project-list-page testId renders without an API error.
vi.mock("@/lib/projectsApi", () => ({
  listProjects: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, size: 100 }),
  triggerScan: vi.fn(),
}));

import { postLogout } from "@/lib/api";
const mockedPostLogout = vi.mocked(postLogout);

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

describe("App smoke (authenticated)", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: fakeUser,
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    useAuthStore.getState().reset();
    window.history.replaceState(null, "", "/");
  });

  it("redirects the / index to /projects (dashboard dropped)", async () => {
    renderAppAt("/");
    await waitFor(() => {
      expect(screen.getByTestId("project-list-page")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("nav-dashboard")).not.toBeInTheDocument();
  });

  it("renders Projects as the first sidebar item", async () => {
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("nav-projects")).toBeInTheDocument();
    });
    expect(screen.getByTestId("nav-projects")).toHaveAttribute(
      "href",
      "/projects",
    );
  });

  it("renders the sidebar navigation links", async () => {
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("nav-projects")).toBeInTheDocument();
    });
    expect(screen.getByTestId("nav-scans")).toBeInTheDocument();
    expect(screen.getByTestId("nav-approvals")).toBeInTheDocument();
  });

  it("toggles the active language between en and ko", async () => {
    const user = userEvent.setup();
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("app-shell")).toBeInTheDocument();
    });

    const toggle = screen.getByTestId("language-toggle");
    expect(toggle).toHaveAttribute("data-current-language", "en");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "ko");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "en");
  });

  it("renders the logout button in the app header", async () => {
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("logout-button")).toBeInTheDocument();
    });
  });

  it("super admin sees the admin nav section with all admin links", async () => {
    useAuthStore.setState({
      user: { ...fakeUser, isSuperuser: true, role: "super_admin" },
      accessToken: "tok-admin",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("nav-admin-users")).toBeInTheDocument();
    });
    expect(screen.getByTestId("nav-admin-teams")).toBeInTheDocument();
    expect(screen.queryByTestId("nav-admin-dt")).not.toBeInTheDocument();
    expect(screen.getByTestId("nav-admin-scans")).toBeInTheDocument();
    expect(screen.getByTestId("nav-admin-disk")).toBeInTheDocument();
    expect(screen.getByTestId("nav-admin-audit")).toBeInTheDocument();
    expect(screen.getByTestId("nav-admin-health")).toBeInTheDocument();
  });

  it("super admin visiting the removed /admin/dt route falls through to AdminNotFound", async () => {
    useAuthStore.setState({
      user: { ...fakeUser, isSuperuser: true, role: "super_admin" },
      accessToken: "tok-admin",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderAppAt("/admin/dt");
    await waitFor(() => {
      expect(screen.getByTestId("admin-not-found")).toBeInTheDocument();
    });
  });

  it("clicking logout clears auth state and navigates to /login", async () => {
    const user = userEvent.setup();
    mockedPostLogout.mockResolvedValue(undefined);
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("logout-button")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("logout-button"));
    await waitFor(() => {
      expect(screen.getByTestId("login-page")).toBeInTheDocument();
    });
    expect(mockedPostLogout).toHaveBeenCalledOnce();
  });
});

describe("App smoke (unauthenticated)", () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: null,
      accessToken: null,
      status: "anonymous",
      isAuthenticated: false,
    });
  });
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("redirects unauthenticated visitors from / to /login", async () => {
    renderAppAt("/");
    await waitFor(() => {
      expect(screen.getByTestId("login-page")).toBeInTheDocument();
    });
  });

  it("falls through unknown paths to /login", async () => {
    renderAppAt("/this-route-does-not-exist");
    await waitFor(() => {
      expect(screen.getByTestId("login-page")).toBeInTheDocument();
    });
  });
});
