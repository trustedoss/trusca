/**
 * ProjectCreatePage — unit tests.
 *
 * Covers form rendering, zod validation (required name, git URL format),
 * successful submission navigation, API error display, and the
 * group-hierarchy Phase 6 combobox that replaced the flat `<select>` --
 * default direct-membership list, live cascade-reaching search, and the
 * active-team-unresolved way-out.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectCreatePage } from "@/features/projects/ProjectCreatePage";
import type { GroupListItem, GroupListPage } from "@/features/groups/api/groupsApi";
import { ProblemError } from "@/lib/problem";
import { useAuthStore } from "@/stores/authStore";
import { useUIStore } from "@/stores/uiStore";

vi.mock("@/lib/projectsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/projectsApi")>();
  return { ...actual, createProject: vi.fn(), listProjects: vi.fn() };
});

vi.mock("@/features/groups/api/groupsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/features/groups/api/groupsApi")>();
  return { ...actual, listGroups: vi.fn() };
});

// useNavigate is wired through MemoryRouter — we spy on it via the mock so we
// can assert the target path without mounting the full App routing tree.
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

import { createProject } from "@/lib/projectsApi";
import { listGroups } from "@/features/groups/api/groupsApi";
const mockedCreateProject = vi.mocked(createProject);
const mockedListGroups = vi.mocked(listGroups);

function group(name: string, overrides: Partial<GroupListItem> = {}): GroupListItem {
  return {
    id: overrides.id ?? `id-${name}`,
    name,
    slug: overrides.slug ?? name.toLowerCase().replace(/\s+/g, "-"),
    description: overrides.description ?? null,
    parent_group_id: overrides.parent_group_id ?? null,
    child_group_count: overrides.child_group_count ?? 0,
    project_count: overrides.project_count ?? 0,
    member_count: overrides.member_count ?? 0,
    updated_at: overrides.updated_at ?? "2026-05-01T00:00:00Z",
  };
}

function groupsPage(items: GroupListItem[]): GroupListPage {
  return { items, total: items.length, page: 1, page_size: 50 };
}

const fakeUser = {
  id: "u1",
  email: "e@e.com",
  displayName: "E",
  role: "developer" as const,
  isActive: true,
  isSuperuser: false,
  teamId: "team-1",
  // A realistic membership list: `teamId` names one of these. It used to be
  // `[]` here, which only "worked" because ProjectCreatePage carried its
  // OWN `user?.teamId ?? ""` fallback alongside `useActiveTeam()`'s -- the
  // exact double-fallback the group-hierarchy Phase 5 fix removes (see
  // ProjectCreatePage.tsx's comment on `teamId`/`activeTeamUnresolved`), so
  // an empty `teams` array here now correctly resolves to no active team at
  // all rather than silently working anyway.
  teams: [{ id: "team-1", name: "Team One", role: "developer" as const }],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <ProjectCreatePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ProjectCreatePage", () => {
  beforeEach(() => {
    mockedCreateProject.mockReset();
    mockedListGroups.mockReset();
    mockNavigate.mockReset();
    useAuthStore.setState({
      user: fakeUser,
      accessToken: "tok-1",
      status: "authenticated",
      isAuthenticated: true,
    });
    useUIStore.setState({ activeTeamId: null });
  });

  it("renders name, description, and git URL fields", () => {
    renderPage();
    expect(screen.getByTestId("project-create-form")).toBeInTheDocument();
    expect(screen.getByTestId("project-name-input")).toBeInTheDocument();
    expect(screen.getByTestId("project-description-input")).toBeInTheDocument();
    expect(screen.getByTestId("project-git-url-input")).toBeInTheDocument();
  });

  it("shows a validation error when the name field is empty on submit", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("project-name-input")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
    });
    // The name error paragraph should be present (use testid to avoid matching the "Name" label)
    expect(
      screen.getByTestId("project-name-error"),
    ).toBeInTheDocument();
  });

  it("shows a validation error when the git URL is not a valid http(s) URL", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.type(screen.getByTestId("project-name-input"), "My Project");
    await user.type(
      screen.getByTestId("project-git-url-input"),
      "not-a-url",
    );
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("project-git-url-input")).toHaveAttribute(
        "aria-invalid",
        "true",
      );
    });
  });

  it("navigates to the new project after successful submission", async () => {
    const user = userEvent.setup();
    mockedCreateProject.mockResolvedValueOnce({
      id: "proj-123",
      team_id: "team-1",
      name: "My Project",
      slug: "my-project",
      description: null,
      git_url: null,
      default_branch: null,
      declared_license: null,
      ai_usage_context: null,
      business_unit: null,
      owner_contact: null,
      distribution_model: null,
      visibility: "team",
      archived_at: null,
      created_by_user_id: "u1",
      latest_scan_id: null,
      latest_scan_status: null,
      severity_summary: null,
      license_category_summary: null,
      created_by_user_name: null,
      team_name: null,
      has_git_credential: false,
      scan_count: 0,
      release_count: 0,
      last_scan_at: null,
      created_at: "2026-05-08T00:00:00Z",
      updated_at: "2026-05-08T00:00:00Z",
    });
    renderPage();
    await user.type(screen.getByTestId("project-name-input"), "My Project");
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith("/projects/proj-123");
    });
    // The default-resolved team (from `useActiveTeam`) is what got submitted.
    expect(mockedCreateProject).toHaveBeenCalledWith(
      expect.objectContaining({ team_id: "team-1" }),
    );
  });

  it("shows the inline error alert when the API returns a ProblemError", async () => {
    const user = userEvent.setup();
    mockedCreateProject.mockRejectedValueOnce(
      new ProblemError("Conflict", {
        status: 409,
        title: "Conflict",
        detail: "A project with this name already exists.",
        problem: null,
      }),
    );
    renderPage();
    await user.type(screen.getByTestId("project-name-input"), "Duplicate");
    await user.click(screen.getByTestId("project-create-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("project-create-error")).toBeInTheDocument();
    });
    // The surface names its own 409 rather than taking the generic conflict
    // wording, and the backend's English detail stays off the screen.
    expect(screen.getByTestId("project-create-error")).toHaveTextContent(
      "A project with this name already exists in the team.",
    );
  });

  it("gives a single-team user a way out of the active-team-unresolved block, not a dead end", async () => {
    // group-hierarchy Phase 5 security review (Medium finding): the team
    // picker used to only render for `teams.length > 1`, so a SINGLE-team
    // user who hit the active-team-unresolved block (e.g. via
    // GroupDetailPage's "New project" from a cascade-only group) saw a
    // permanently disabled submit button with no control on this page that
    // could clear it -- `fakeUser` here has exactly one membership, the
    // shape that exposes the gap. Phase 6 replaces the `<select>` with the
    // combobox, but the way-out contract is identical: pick a real team from
    // the (always-shown) picker to clear the block.
    useUIStore.setState({ activeTeamId: "group-c-cascade-only" });
    const user = userEvent.setup();
    renderPage();

    const alert = await screen.findByTestId("project-create-no-team");
    expect(alert).toHaveAttribute("data-reason", "active_team_unresolved");
    expect(screen.getByTestId("project-create-submit")).toBeDisabled();

    // The trigger shows the placeholder -- not a silently pre-selected name
    // -- while nothing is actually selected yet.
    const trigger = screen.getByTestId("project-team-combobox-trigger");
    expect(trigger).toHaveTextContent("Select a team");

    await user.click(trigger);
    const option = await screen.findByTestId("project-team-combobox-option");
    expect(option).toHaveAttribute("data-group-id", "team-1");
    await user.click(option);

    expect(screen.getByTestId("project-team-combobox-trigger")).toHaveTextContent(
      "Team One",
    );
    expect(screen.getByTestId("project-create-submit")).not.toBeDisabled();
    expect(screen.queryByTestId("project-create-no-team")).toBeNull();
  });

  it("searching finds and lets you pick a group outside user.teams", async () => {
    // This is the whole point of Phase 6: a group reachable only through the
    // permission cascade (an ancestor membership, not a direct one) has no
    // entry in `user.teams`, so it can ONLY be reached through the search
    // path -- `GET /v1/groups?q=` already spans the caller's whole
    // accessible set, cascade included, per the backend's `can_access_group`.
    mockedListGroups.mockResolvedValueOnce(
      groupsPage([
        group("Cascade Reachable Group", {
          id: "group-cascade-1",
          slug: "cascade-reachable-group",
        }),
      ]),
    );
    mockedCreateProject.mockResolvedValueOnce({
      id: "proj-999",
      team_id: "group-cascade-1",
      name: "Cascade Project",
      slug: "cascade-project",
      description: null,
      git_url: null,
      default_branch: null,
      declared_license: null,
      ai_usage_context: null,
      business_unit: null,
      owner_contact: null,
      distribution_model: null,
      visibility: "team",
      archived_at: null,
      created_by_user_id: "u1",
      latest_scan_id: null,
      latest_scan_status: null,
      severity_summary: null,
      license_category_summary: null,
      created_by_user_name: null,
      team_name: null,
      has_git_credential: false,
      scan_count: 0,
      release_count: 0,
      last_scan_at: null,
      created_at: "2026-05-08T00:00:00Z",
      updated_at: "2026-05-08T00:00:00Z",
    });

    const user = userEvent.setup();
    renderPage();

    const trigger = screen.getByTestId("project-team-combobox-trigger");
    await user.click(trigger);

    // Default list (no search term) is `user.teams` -- zero extra requests.
    expect(mockedListGroups).not.toHaveBeenCalled();
    expect(screen.getByTestId("project-team-combobox-option")).toHaveAttribute(
      "data-group-id",
      "team-1",
    );

    await user.type(
      screen.getByTestId("project-team-combobox-search"),
      "Cascade",
    );

    await waitFor(
      () => {
        expect(mockedListGroups).toHaveBeenCalledWith({ q: "Cascade" });
      },
      { timeout: 2000 },
    );

    const option = await screen.findByTestId("project-team-combobox-option");
    expect(option).toHaveAttribute("data-group-id", "group-cascade-1");
    expect(option).toHaveTextContent("Cascade Reachable Group");
    expect(option).toHaveTextContent("cascade-reachable-group");

    await user.click(option);

    expect(screen.getByTestId("project-team-combobox-trigger")).toHaveTextContent(
      "Cascade Reachable Group",
    );

    await user.type(screen.getByTestId("project-name-input"), "Cascade Project");
    await user.click(screen.getByTestId("project-create-submit"));

    await waitFor(() => {
      expect(mockedCreateProject).toHaveBeenCalledWith(
        expect.objectContaining({ team_id: "group-cascade-1" }),
      );
    });
  });

  it("shows an empty state when a search matches nothing", async () => {
    mockedListGroups.mockResolvedValueOnce(groupsPage([]));
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByTestId("project-team-combobox-trigger"));
    await user.type(
      screen.getByTestId("project-team-combobox-search"),
      "nonexistent",
    );

    await waitFor(
      () => {
        expect(mockedListGroups).toHaveBeenCalledWith({ q: "nonexistent" });
      },
      { timeout: 2000 },
    );

    expect(
      await screen.findByTestId("project-team-combobox-empty"),
    ).toBeInTheDocument();
  });

  it("does not search below the 2-character floor, mirroring the backend's own", async () => {
    // Security review: GET /v1/groups had no rate limit or query-length
    // floor before this combobox turned it into a per-keystroke caller.
    // services.group_directory_service._MIN_SEARCH_QUERY_LEN=2 now soft-fails
    // a shorter query server-side; this pins that the combobox never even
    // FIRES that request for a 1-character term, showing a "keep typing"
    // hint instead of the search-empty state (which would otherwise be
    // indistinguishable from a real "no matches" result).
    const user = userEvent.setup();
    renderPage();

    await user.click(screen.getByTestId("project-team-combobox-trigger"));
    await user.type(screen.getByTestId("project-team-combobox-search"), "a");

    await waitFor(() => {
      expect(
        screen.getByTestId("project-team-combobox-min-length-hint"),
      ).toBeInTheDocument();
    });
    // Waited past the 300ms debounce already (waitFor above); confirm no
    // request went out for the sub-floor term.
    expect(mockedListGroups).not.toHaveBeenCalled();
  });
});
