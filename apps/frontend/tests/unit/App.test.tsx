import { readFileSync } from "node:fs";
import path from "node:path";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "@/App";
import { AppProviders } from "@/components/AppProviders";
import { useAuthStore, type AuthUser } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

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

/**
 * Reads the admin child routes straight out of `router.tsx`.
 *
 * A hand-kept list here would drift the same way the sidebar did: someone
 * adds a route, forgets the nav entry, and forgets the list that was supposed
 * to notice. Parsing the source means a new route joins this check by
 * existing.
 */
function adminRoutesFromRouter(): string[] {
  const source = readFileSync(
    path.resolve(__dirname, "../../src/router.tsx"),
    "utf8",
  );
  const opening = '<Route path="admin"';
  const block = source.slice(
    // After the opening tag, so the admin parent itself is not read as one of
    // its own children.
    source.indexOf(opening) + opening.length,
    source.indexOf('<Route path="*" element={<AdminNotFound />} />'),
  );
  expect(block, "the admin route block moved; this parser needs a look").not.toBe(
    "",
  );
  // `[^"]+` rather than a friendly character class: a narrow one skips the
  // route it does not recognise instead of failing on it, so `oauth2` or a
  // camelCase segment would drop out of the check silently, which is the
  // exact shape of the bug the check exists to catch. Wildcards and path
  // parameters are excluded on purpose - they match by pattern, not by a URL
  // a sidebar entry could point at.
  const paths = [...block.matchAll(/<Route\s+path="([^"]+)"/g)]
    .map((match) => match[1])
    .filter((route) => !route.startsWith("*") && !route.includes(":"))
    .map((route) => `/admin/${route}`);
  // A parser that matched nothing would turn this into a test that asserts an
  // empty loop. The floor is the count at the time of writing, minus room to
  // delete one.
  expect(paths.length).toBeGreaterThanOrEqual(6);
  return paths;
}

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

    // C1: theme and language moved into the profile menu, which is what
    // made them reachable below 640px at all.
    await user.click(screen.getByTestId("header-profile-menu"));
    const toggle = await screen.findByTestId("language-toggle");
    expect(toggle).toHaveAttribute("data-current-language", "en");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "ko");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("data-current-language", "en");
  });

  it("offers sign-out from the profile menu", async () => {
    // C1: it used to sit exposed beside the avatar, one careless click from
    // the app's only sign-out. Behind the menu it needs an intent.
    const user = userEvent.setup();
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("app-shell")).toBeInTheDocument();
    });

    expect(screen.queryByTestId("logout-button")).toBeNull();
    await user.click(screen.getByTestId("header-profile-menu"));
    expect(await screen.findByTestId("logout-button")).toBeInTheDocument();
  });

  // M-17 — header initials avatar + active team label.
  it("renders the header avatar with initials derived from the display name", async () => {
    useAuthStore.setState({
      user: { ...fakeUser, displayName: "Alice Smith" },
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
    const user = userEvent.setup();
    renderAppAt("/projects");
    const avatar = await screen.findByTestId("header-avatar");
    expect(avatar.textContent).toBe("AS");
    // The profile link moved into the menu the avatar now opens; it stays
    // reachable for ProfileHarness / docs-uat once the menu is open.
    await user.click(screen.getByTestId("header-profile-menu"));
    expect(await screen.findByTestId("header-profile-link")).toHaveAttribute(
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

  it("shows the active team in the global bar, and lets a multi-team user switch", async () => {
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

    // W14 — the team label moved out of the profile link and into the
    // global bar, as a switcher for users who belong to more than one.
    const switcher = await screen.findByTestId("topbar-team-switcher");
    expect(switcher.textContent).toContain("Security");

    await userEvent.click(switcher);
    await userEvent.click(await screen.findByTestId("topbar-team-option-team-1"));

    await waitFor(() => {
      expect(useUIStore.getState().activeTeamId).toBe("team-1");
    });
    expect(
      (await screen.findByTestId("topbar-team-switcher")).textContent,
    ).toContain("Platform");
  });

  it("shows a plain label, not a switcher, for a single-team user", async () => {
    useAuthStore.setState({
      user: {
        ...fakeUser,
        teamId: "team-1",
        teams: [{ id: "team-1", name: "Platform", role: "developer" }],
      },
      accessToken: "tok-app",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderAppAt("/projects");

    expect((await screen.findByTestId("topbar-team")).textContent).toBe(
      "Platform",
    );
    expect(screen.queryByTestId("topbar-team-switcher")).toBeNull();
  });

  it("omits the team label entirely when the user has no memberships", async () => {
    // fakeUser ships teamId: null / teams: [] — e.g. the seeded super admin.
    renderAppAt("/projects");
    await waitFor(() => {
      expect(screen.getByTestId("header-avatar")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("topbar-team")).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("topbar-team-switcher"),
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
    // C1: this one existed as a route, a page, both translations and an e2e
    // spec, and had no link. Nothing failed while it was unreachable, so the
    // href is asserted here rather than just the presence of the row.
    expect(screen.getByTestId("nav-admin-backup")).toHaveAttribute(
      "href",
      "/admin/backup",
    );
  });

  it("every admin nav entry points at a route that exists", async () => {
    // The gap this closes is not a broken link but a missing one, and the
    // check that would have caught it is the reverse direction: every admin
    // route the app defines should be reachable from the sidebar.
    useAuthStore.setState({
      user: { ...fakeUser, isSuperuser: true, role: "super_admin" },
      accessToken: "tok-admin",
      status: "authenticated",
      isAuthenticated: true,
    });
    renderAppAt("/projects");
    await screen.findByTestId("nav-admin-users");

    for (const route of adminRoutesFromRouter()) {
      const link = screen
        .getAllByRole("link")
        .find((el) => el.getAttribute("href") === route);
      expect(
        link,
        `no sidebar entry links to ${route}; the page is live but unreachable`,
      ).toBeDefined();
    }
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
      expect(screen.getByTestId("app-shell")).toBeInTheDocument();
    });
    await user.click(screen.getByTestId("header-profile-menu"));
    await user.click(await screen.findByTestId("logout-button"));
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
