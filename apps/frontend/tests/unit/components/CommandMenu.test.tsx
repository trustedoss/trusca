/**
 * CommandMenu — unit tests for the global ⌘K palette (W9-#54).
 *
 * Coverage:
 *   - ⌘K opens; Esc closes.
 *   - Placeholder + group headings render with i18n strings.
 *   - "No results" message when API returns empty.
 *   - Typing in the input triggers a debounced API call.
 *   - Selecting a project navigates to /projects/:id and closes the palette.
 *   - Admin pages are hidden for non-super-admin users.
 *   - Admin pages render for super-admin users.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandMenu, CommandMenuTrigger } from "@/components/CommandMenu";
import { useAuthStore } from "@/stores/authStore";

vi.mock("@/lib/projectsApi", async () => {
  return {
    listProjects: vi.fn(),
  };
});

vi.mock("@/lib/searchApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/searchApi")>("@/lib/searchApi");
  return {
    ...actual,
    globalSearch: vi.fn(),
  };
});

import { listProjects } from "@/lib/projectsApi";
import {
  globalSearch,
  type GlobalSearchResults,
  type SearchComponentHit,
  type SearchVulnerabilityHit,
} from "@/lib/searchApi";

const mockedListProjects = vi.mocked(listProjects);
const mockedGlobalSearch = vi.mocked(globalSearch);

function componentHit(
  over: Partial<SearchComponentHit> = {},
): SearchComponentHit {
  return {
    project_id: "proj-abc",
    project_name: "test-project",
    project_slug: "test-project",
    component_name: "lodash",
    version: "4.17.21",
    purl: "pkg:npm/lodash@4.17.21",
    ...over,
  };
}

function vulnHit(
  over: Partial<SearchVulnerabilityHit> = {},
): SearchVulnerabilityHit {
  return {
    project_id: "proj-abc",
    project_name: "test-project",
    project_slug: "test-project",
    cve_id: "CVE-2021-23337",
    severity: "high",
    ...over,
  };
}

function searchResults(
  over: Partial<GlobalSearchResults> = {},
): GlobalSearchResults {
  return {
    query: "",
    components: [],
    vulnerabilities: [],
    ...over,
  };
}

// Tracks the URL inside MemoryRouter so we can assert navigations. Has to
// live inside the <MemoryRouter> tree so useLocation sees the in-memory
// history (window.location stays "/" because MemoryRouter doesn't touch it).
function LocationProbe({
  onLocation,
  onFullLocation,
}: {
  onLocation?: (path: string) => void;
  onFullLocation?: (loc: { pathname: string; search: string }) => void;
}) {
  const location = useLocation();
  onLocation?.(location.pathname);
  onFullLocation?.({ pathname: location.pathname, search: location.search });
  return <div data-testid="location-probe" data-pathname={location.pathname} />;
}

interface HarnessProps {
  open: boolean;
  onOpenChange?: (open: boolean) => void;
  initialEntries?: string[];
  onLocation?: (path: string) => void;
  onFullLocation?: (loc: { pathname: string; search: string }) => void;
  withTrigger?: boolean;
}

function Harness({
  open,
  onOpenChange,
  initialEntries = ["/projects"],
  onLocation,
  onFullLocation,
  withTrigger,
}: HarnessProps) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={initialEntries}>
        {withTrigger ? (
          <CommandMenuTrigger onOpen={() => onOpenChange?.(true)} />
        ) : null}
        <CommandMenu open={open} onOpenChange={onOpenChange ?? (() => {})} />
        {onLocation || onFullLocation ? (
          <LocationProbe
            onLocation={onLocation}
            onFullLocation={onFullLocation}
          />
        ) : null}
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function setUser(opts: { superuser: boolean }): void {
  useAuthStore.setState({
    user: {
      id: "u-1",
      email: "u@example.com",
      displayName: "User",
      role: opts.superuser ? "super_admin" : "developer",
      isActive: true,
      isSuperuser: opts.superuser,
      teamId: null,
      teams: [],
    },
    accessToken: "tok",
    status: "authenticated",
    isAuthenticated: true,
  });
}

function emptyProjectsResponse() {
  return { items: [], total: 0, page: 1, size: 10 } as ReturnType<
    typeof listProjects
  > extends Promise<infer R>
    ? R
    : never;
}

// Wrap to render with a controlled-open wrapper so we can drive open/close
// via local React state (matching the AppShell pattern).
function ControlledHarness({
  initiallyOpen,
  initialEntries,
  onLocation,
  onFullLocation,
  withTrigger,
}: {
  initiallyOpen: boolean;
  initialEntries?: string[];
  onLocation?: (path: string) => void;
  onFullLocation?: (loc: { pathname: string; search: string }) => void;
  withTrigger?: boolean;
}): ReactNode {
  return (
    <ControlledHarnessInner
      initiallyOpen={initiallyOpen}
      initialEntries={initialEntries}
      onLocation={onLocation}
      onFullLocation={onFullLocation}
      withTrigger={withTrigger}
    />
  );
}

// Separate component so we can use hooks.
import { useState } from "react";
function ControlledHarnessInner({
  initiallyOpen,
  initialEntries,
  onLocation,
  onFullLocation,
  withTrigger,
}: {
  initiallyOpen: boolean;
  initialEntries?: string[];
  onLocation?: (path: string) => void;
  onFullLocation?: (loc: { pathname: string; search: string }) => void;
  withTrigger?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <Harness
      open={open}
      onOpenChange={setOpen}
      initialEntries={initialEntries}
      onLocation={onLocation}
      onFullLocation={onFullLocation}
      withTrigger={withTrigger}
    />
  );
}

describe("CommandMenu", () => {
  beforeEach(() => {
    mockedListProjects.mockReset();
    mockedListProjects.mockResolvedValue(emptyProjectsResponse());
    mockedGlobalSearch.mockReset();
    mockedGlobalSearch.mockResolvedValue(searchResults());
    setUser({ superuser: false });
  });

  afterEach(() => {
    useAuthStore.getState().reset();
  });

  it("renders the placeholder and pages group heading when open", async () => {
    render(<ControlledHarness initiallyOpen={true} />);

    // The placeholder text is on the cmdk input.
    expect(
      await screen.findByPlaceholderText("Search projects, CVEs, pages..."),
    ).toBeInTheDocument();

    // The Pages group is always rendered because it's a static catalog.
    expect(await screen.findByText("Pages")).toBeInTheDocument();
  });

  it("shows 'no results' when the input does not match any item", async () => {
    const user = userEvent.setup();
    render(<ControlledHarness initiallyOpen={true} />);

    const input = await screen.findByPlaceholderText(
      "Search projects, CVEs, pages...",
    );
    await user.type(input, "zzz-nonexistent-query");

    // cmdk's CommandEmpty renders the i18n message when no items match.
    expect(await screen.findByText("No results found.")).toBeInTheDocument();
  });

  it("triggers an API call for projects when the user types (debounced)", async () => {
    const user = userEvent.setup();
    mockedListProjects.mockResolvedValue({
      items: [
        {
          id: "p-1",
          team_id: "t-1",
          name: "frontend-admin",
          slug: "frontend-admin",
          description: null,
          git_url: null,
          default_branch: null,
          visibility: "team",
          archived_at: null,
          created_by_user_id: null,
          latest_scan_id: null,
          latest_scan_status: null,
          severity_summary: null,
          license_category_summary: null,
          created_by_user_name: null,
          has_git_credential: false,
          scan_count: 0,
          release_count: 0,
          last_scan_at: null,
          created_at: "2026-05-27T00:00:00Z",
          updated_at: "2026-05-27T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      size: 10,
    });

    render(<ControlledHarness initiallyOpen={true} />);

    const input = await screen.findByPlaceholderText(
      "Search projects, CVEs, pages...",
    );
    await user.type(input, "front");

    // After the 200ms debounce fires, the API should have been called with
    // q: "front". Two calls in total: one initial open (q: undefined) and
    // the debounced search.
    await waitFor(
      () => {
        const calls = mockedListProjects.mock.calls;
        const matched = calls.some((c) => c[0]?.q === "front");
        expect(matched).toBe(true);
      },
      { timeout: 1500 },
    );

    // The project row renders in the Projects group. cmdk filters items
    // client-side too — the `value="frontend-admin frontend-admin"` we set
    // on CommandItem matches the typed "front".
    expect(
      await screen.findByTestId("command-menu-project-p-1"),
    ).toBeInTheDocument();
  });

  it("navigates to /projects/:id when a project result is selected", async () => {
    const user = userEvent.setup();
    mockedListProjects.mockResolvedValue({
      items: [
        {
          id: "proj-abc",
          team_id: "t-1",
          name: "test-project",
          slug: "test-project",
          description: null,
          git_url: null,
          default_branch: null,
          visibility: "team",
          archived_at: null,
          created_by_user_id: null,
          latest_scan_id: null,
          latest_scan_status: null,
          severity_summary: null,
          license_category_summary: null,
          created_by_user_name: null,
          has_git_credential: false,
          scan_count: 0,
          release_count: 0,
          last_scan_at: null,
          created_at: "2026-05-27T00:00:00Z",
          updated_at: "2026-05-27T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      size: 10,
    });

    const locations: string[] = [];
    render(
      <ControlledHarness
        initiallyOpen={true}
        onLocation={(p) => locations.push(p)}
      />,
    );

    const item = await screen.findByTestId("command-menu-project-proj-abc");
    await user.click(item);

    await waitFor(() => {
      expect(locations).toContain("/projects/proj-abc");
    });
  });

  it("hides admin routes for non-super-admin users", async () => {
    setUser({ superuser: false });
    render(<ControlledHarness initiallyOpen={true} />);

    // Wait for the palette to mount.
    await screen.findByText("Pages");

    // Admin route entries should not be in the document.
    expect(
      screen.queryByTestId("command-menu-route-/admin/users"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("command-menu-route-/admin/health"),
    ).not.toBeInTheDocument();

    // Non-admin routes remain visible.
    expect(
      screen.getByTestId("command-menu-route-/projects"),
    ).toBeInTheDocument();
  });

  it("shows admin routes for super-admin users", async () => {
    setUser({ superuser: true });
    render(<ControlledHarness initiallyOpen={true} />);

    expect(
      await screen.findByTestId("command-menu-route-/admin/users"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("command-menu-route-/admin/health"),
    ).toBeInTheDocument();
  });

  it("opens via ⌘K and closes via Esc", async () => {
    const user = userEvent.setup();
    render(<ControlledHarnessWithShortcut />);

    // Not open initially — placeholder absent.
    expect(
      screen.queryByPlaceholderText("Search projects, CVEs, pages..."),
    ).not.toBeInTheDocument();

    // Press ⌘K.
    await user.keyboard("{Meta>}k{/Meta}");

    expect(
      await screen.findByPlaceholderText("Search projects, CVEs, pages..."),
    ).toBeInTheDocument();

    // Esc closes the dialog.
    await user.keyboard("{Escape}");

    await waitFor(() => {
      expect(
        screen.queryByPlaceholderText("Search projects, CVEs, pages..."),
      ).not.toBeInTheDocument();
    });
  });

  it("opens via header trigger button click", async () => {
    const user = userEvent.setup();
    render(<ControlledHarness initiallyOpen={false} withTrigger />);

    const trigger = screen.getByTestId("command-menu-trigger");
    expect(trigger).toBeInTheDocument();

    await user.click(trigger);

    expect(
      await screen.findByPlaceholderText("Search projects, CVEs, pages..."),
    ).toBeInTheDocument();
  });

  // --- BomLens parity Phase H-2: cross-project component/CVE search ---------

  it("does NOT call the global-search endpoint below the 2-char threshold", async () => {
    const user = userEvent.setup();
    render(<ControlledHarness initiallyOpen={true} />);

    const input = await screen.findByPlaceholderText(
      "Search projects, CVEs, pages...",
    );
    // A single character is under SEARCH_MIN_CHARS — projects still search, but
    // the heavier global-search endpoint must stay idle.
    await user.type(input, "l");

    // Give the 200ms debounce room to fire before asserting the negative.
    await new Promise((resolve) => setTimeout(resolve, 350));
    expect(mockedGlobalSearch).not.toHaveBeenCalled();
  });

  it("calls global-search with the debounced query once it clears 2 chars", async () => {
    const user = userEvent.setup();
    mockedGlobalSearch.mockResolvedValue(
      searchResults({ components: [componentHit()] }),
    );
    render(<ControlledHarness initiallyOpen={true} />);

    const input = await screen.findByPlaceholderText(
      "Search projects, CVEs, pages...",
    );
    await user.type(input, "lodash");

    await waitFor(
      () => {
        const matched = mockedGlobalSearch.mock.calls.some(
          (c) => c[0] === "lodash",
        );
        expect(matched).toBe(true);
      },
      { timeout: 1500 },
    );
  });

  it("renders component hits under the Components group and deep-links into the components tab", async () => {
    const user = userEvent.setup();
    mockedGlobalSearch.mockResolvedValue(
      searchResults({ components: [componentHit()] }),
    );

    const locations: Array<{ pathname: string; search: string }> = [];
    render(
      <ControlledHarness
        initiallyOpen={true}
        onFullLocation={(loc) => locations.push(loc)}
      />,
    );

    const input = await screen.findByPlaceholderText(
      "Search projects, CVEs, pages...",
    );
    await user.type(input, "lodash");

    // Group heading + row both carry stable testids.
    expect(
      await screen.findByTestId("command-menu-group-components"),
    ).toBeInTheDocument();
    const row = await screen.findByTestId("command-menu-component-proj-abc-0");
    await user.click(row);

    await waitFor(() => {
      const hit = locations.find(
        (l) => l.pathname === "/projects/proj-abc",
      );
      expect(hit).toBeDefined();
      expect(hit?.search).toBe("?tab=components&search=lodash");
    });
  });

  it("renders CVE hits under the CVEs group and deep-links into the vulnerabilities tab", async () => {
    const user = userEvent.setup();
    mockedGlobalSearch.mockResolvedValue(
      searchResults({ vulnerabilities: [vulnHit()] }),
    );

    const locations: Array<{ pathname: string; search: string }> = [];
    render(
      <ControlledHarness
        initiallyOpen={true}
        onFullLocation={(loc) => locations.push(loc)}
      />,
    );

    const input = await screen.findByPlaceholderText(
      "Search projects, CVEs, pages...",
    );
    await user.type(input, "CVE-2021-23337");

    expect(
      await screen.findByTestId("command-menu-group-cves"),
    ).toBeInTheDocument();
    const row = await screen.findByTestId(
      "command-menu-cve-proj-abc-CVE-2021-23337",
    );
    // Severity label text is present alongside the color dot (a11y: color is
    // not the sole signal).
    expect(row).toHaveTextContent("high");

    await user.click(row);

    await waitFor(() => {
      const hit = locations.find(
        (l) => l.pathname === "/projects/proj-abc",
      );
      expect(hit).toBeDefined();
      expect(hit?.search).toBe(
        "?tab=vulnerabilities&search=CVE-2021-23337",
      );
    });
  });

  it("clears component/CVE hits when the query drops back below 2 chars", async () => {
    const user = userEvent.setup();
    mockedGlobalSearch.mockResolvedValue(
      searchResults({ components: [componentHit()] }),
    );
    render(<ControlledHarness initiallyOpen={true} />);

    const input = await screen.findByPlaceholderText(
      "Search projects, CVEs, pages...",
    );
    await user.type(input, "lodash");
    expect(
      await screen.findByTestId("command-menu-component-proj-abc-0"),
    ).toBeInTheDocument();

    // Erase down to a single character — the Components group must disappear.
    await user.clear(input);
    await user.type(input, "l");

    await waitFor(() => {
      expect(
        screen.queryByTestId("command-menu-group-components"),
      ).not.toBeInTheDocument();
    });
  });
});

// Variant that exercises the useCommandMenuShortcut hook end-to-end.
import { useCommandMenuShortcut } from "@/components/CommandMenu";
function ControlledHarnessWithShortcut() {
  const { open, setOpen } = useCommandMenuShortcut();
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects"]}>
        <CommandMenu open={open} onOpenChange={setOpen} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}
