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
  // M-15: LoginPage queries provider availability on mount. Resolve with an
  // empty list so the OAuth section simply stays hidden in App smoke tests.
  fetchOAuthProviders: vi.fn().mockResolvedValue({ providers: [] }),
}));

// ProjectListPage AND DashboardPage both call listProjects — mock it so the
// test doesn't hit the network and so the page test ids render without an API
// error. DashboardPage additionally hits listApprovals + listMyScans, so we
// provide those too. All return empty lists so the dashboard renders its
// empty-state CTA path on the / index.
vi.mock("@/lib/projectsApi", () => ({
  // Module-level constant consumed by AdminScansPage's KIND_OPTIONS; the
  // wholesale mock must re-export it or the route tree fails to import.
  SCAN_KIND_VALUES: ["source", "container", "sbom"] as const,
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

  it("renders the dedicated Dashboard at the / index (W9-#50 D1-001)", async () => {
    renderAppAt("/");
    await waitFor(() => {
      expect(screen.getByTestId("dashboard-page")).toBeInTheDocument();
    });
    // Dashboard is the first sidebar item — Projects sits below it.
    expect(screen.getByTestId("nav-dashboard")).toBeInTheDocument();
    expect(screen.getByTestId("nav-projects")).toBeInTheDocument();
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

  // M-17 — header initials avatar + active team label.
  it("renders the header avatar with initials derived from the display name", async () => {
    useAuthStore.setState({
      user: { ...fakeUser, displayName: "Alice Smith" },
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderAppAt("/projects");
    const avatar = await screen.findByTestId("header-avatar");
    expect(avatar.textContent).toBe("AS");
    // The profile link itself stays reachable for ProfileHarness / docs-uat.
    expect(screen.getByTestId("header-profile-link")).toHaveAttribute(
      "href",
      "/profile",
    );
  });

  it("falls back to the email local part for the avatar initial", async () => {
    useAuthStore.setState({
      user: { ...fakeUser, displayName: "dev@x.com", email: "dev@x.com" },
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderAppAt("/projects");
    const avatar = await screen.findByTestId("header-avatar");
    expect(avatar.textContent).toBe("D");
  });

  it("shows the active team (matching teamId) in the header profile area", async () => {
    useAuthStore.setState({
      user: {
        ...fakeUser,
        teamId: "team-2",
        teams: [
          { id: "team-1", name: "Platform", role: "developer" },
          { id: "team-2", name: "Security", role: "developer" },
        ],
      },
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderAppAt("/projects");
    const team = await screen.findByTestId("header-active-team");
    expect(team.textContent).toBe("Security");
  });

  it("omits the team label entirely when the user has no memberships", async () => {
    // fakeUser ships teamId: null / teams: [] — e.g. the seeded super admin.
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("header-avatar")).toBeInTheDocument();
    });
    expect(
      screen.queryByTestId("header-active-team"),
    ).not.toBeInTheDocument();
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
