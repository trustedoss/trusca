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

  it("applies the W12-C route-change entrance animation to <main>", async () => {
    renderAppAt("/projects");
    const main = await screen.findByTestId("app-main");
    // <main> is keyed on the pathname and carries the 250 ms fade-in entrance.
    expect(main.className).toContain("animate-in");
    expect(main.className).toContain("fade-in-0");
    expect(main.className).toContain("duration-slow");
  });
});

describe("AppShell - skip link and profile menu (C1)", () => {
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
    window.history.replaceState(null, "", "/");
  });

  it("puts the skip link first in the tab order and points it at <main>", async () => {
    renderAppAt("/projects");
    const skip = await screen.findByTestId("skip-to-content");
    // Every screen puts ~20 nav links between the page top and the content;
    // a keyboard reader had to walk all of them on every navigation.
    const shell = screen.getByTestId("app-shell");
    const focusable = shell.querySelectorAll("a[href], button");
    expect(focusable[0]).toBe(skip);
    expect(skip).toHaveAttribute("href", "#main-content");

    const main = screen.getByTestId("app-main");
    expect(main.id).toBe("main-content");
    // Without tabindex the fragment jump moves the scroll but not focus, so
    // the next Tab would start from the top of the document again.
    expect(main).toHaveAttribute("tabindex", "-1");
  });

  it("keeps the skip link out of sight until it is focused", async () => {
    const user = userEvent.setup();
    renderAppAt("/projects");
    const skip = await screen.findByTestId("skip-to-content");
    expect(skip.className).toContain("sr-only");
    // focus-visible would hide it from a reader who tabs in with a mouse
    // already in play; plain focus is what a skip link needs.
    expect(skip.className).toContain("focus:not-sr-only");

    await user.tab();
    expect(skip).toHaveFocus();
  });

  it("gathers profile, sign-out, theme and language behind one menu", async () => {
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-shell");

    for (const id of [
      "header-profile-link",
      "logout-button",
      "theme-toggle",
      "language-toggle",
    ]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }

    await user.click(screen.getByTestId("header-profile-menu"));
    for (const id of [
      "header-profile-link",
      "logout-button",
      "theme-toggle",
      "language-toggle",
      "header-docs-link",
      "header-shortcuts-link",
    ]) {
      expect(await screen.findByTestId(id)).toBeInTheDocument();
    }
  });

  it("gives every control in the menu a menuitem role", async () => {
    // A Radix menu swallows Tab and moves focus only between the children it
    // registered as items. Anything rendered in there as a plain button is
    // reachable by mouse and by nothing else, which is how theme and language
    // came out of this change less reachable than they went in.
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-shell");

    await user.click(screen.getByTestId("header-profile-menu"));
    for (const id of [
      "header-profile-link",
      "header-docs-link",
      "header-shortcuts-link",
      "theme-toggle",
      "language-toggle",
      "logout-button",
    ]) {
      expect(await screen.findByTestId(id)).toHaveAttribute(
        "role",
        "menuitem",
      );
    }
  });

  it("walks the arrow keys onto the theme and language rows", async () => {
    // The role alone is not the whole contract: Radix registers an item in
    // its roving-focus group by ref, so a component it cannot get a ref to
    // carries the role and still never receives focus.
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-shell");

    await user.click(screen.getByTestId("header-profile-menu"));
    await screen.findByTestId("logout-button");

    const reached = new Set<string>();
    // Generous rather than exact: a fixed count sized to today's six rows
    // turns into a false failure the day someone adds a seventh above them.
    for (let press = 0; press < 20; press += 1) {
      await user.keyboard("{ArrowDown}");
      const testId = document.activeElement?.getAttribute("data-testid");
      if (testId) reached.add(testId);
    }

    expect(reached).toContain("theme-toggle");
    expect(reached).toContain("language-toggle");
  });

  it("keeps the menu open while cycling theme and language", async () => {
    // Both are cycles rather than destinations, so a menu that closed on the
    // first press would make the second press a fresh journey.
    //
    // Two things have to be true at once, and each can hide the other: the
    // row's own handler has to run (the value cycles) AND the handler Radix
    // passed down has to run (which fires onSelect, which the menu keeps open
    // by preventing). A spread that lets one declaration win drops the other,
    // so the second press is asserted here rather than only the first.
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-shell");

    await user.click(screen.getByTestId("header-profile-menu"));
    const language = await screen.findByTestId("language-toggle");
    expect(language).toHaveAttribute("data-current-language", "en");

    await user.click(screen.getByTestId("language-toggle"));
    expect(screen.getByTestId("language-toggle")).toHaveAttribute(
      "data-current-language",
      "ko",
    );

    await user.click(screen.getByTestId("language-toggle"));
    expect(screen.getByTestId("language-toggle")).toHaveAttribute(
      "data-current-language",
      "en",
    );

    // The theme row is still there to be pressed, and still cycles.
    const themeBefore = screen
      .getByTestId("theme-toggle")
      .getAttribute("data-theme-preference");
    await user.click(screen.getByTestId("theme-toggle"));
    expect(
      screen.getByTestId("theme-toggle").getAttribute("data-theme-preference"),
    ).not.toBe(themeBefore);
  });

  it("closes the menu on a row that goes somewhere", async () => {
    // The counterpart to the test above: `onSelect` reaching Radix at all is
    // what makes theme and language special, and a menu that never closed
    // would make that distinction meaningless.
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-shell");

    await user.click(screen.getByTestId("header-profile-menu"));
    await user.click(await screen.findByTestId("header-profile-link"));

    await waitFor(() => {
      expect(screen.queryByTestId("logout-button")).toBeNull();
    });
  });

  it("opens the shortcut sheet from the profile menu", async () => {
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-shell");

    await user.click(screen.getByTestId("header-profile-menu"));
    await user.click(await screen.findByTestId("header-shortcuts-link"));

    // The `?` binding is undiscoverable on its own, so the menu entry is how
    // a reader who never guesses it finds out the shortcuts exist.
    expect(await screen.findByTestId("shortcut-help-dialog")).toBeInTheDocument();
  });

  it("sends the docs link off-site with the usual new-tab hardening", async () => {
    const user = userEvent.setup();
    renderAppAt("/projects");
    await screen.findByTestId("app-shell");

    await user.click(screen.getByTestId("header-profile-menu"));
    const docs = await screen.findByTestId("header-docs-link");
    expect(docs).toHaveAttribute("target", "_blank");
    expect(docs).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });
});
