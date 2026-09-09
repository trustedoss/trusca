/**
 * GroupDetailPage — unit tests, group-hierarchy Phase 4 PR 4-B.
 *
 * Covers: the existence-hide 404 branch renders no page frame at all, a
 * successful load renders the name/breadcrumb/stats, ancestor breadcrumb
 * segments are clickable links, subgroups/projects/members tabs each fetch
 * their own data, and "New project" resolves the target group through
 * `uiStore.setActiveTeamId` (the one channel `ProjectCreatePage` actually
 * reads) rather than a URL prefill.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GroupDetail } from "@/features/groups/api/groupsApi";

vi.mock("@/features/groups/api/groupsApi", () => ({
  getGroup: vi.fn(),
  listGroups: vi.fn(),
  getGroupMembers: vi.fn(),
}));

vi.mock("@/lib/projectsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/projectsApi")>(
    "@/lib/projectsApi",
  );
  return { ...actual, listProjects: vi.fn() };
});

import {
  getGroup,
  getGroupMembers,
  listGroups,
} from "@/features/groups/api/groupsApi";
import { GroupDetailPage } from "@/features/groups/GroupDetailPage";
import { listProjects } from "@/lib/projectsApi";
import { ProblemError } from "@/lib/problem";
import { useUIStore } from "@/stores/uiStore";

const mockedGetGroup = vi.mocked(getGroup);
const mockedListGroups = vi.mocked(listGroups);
const mockedGetMembers = vi.mocked(getGroupMembers);
const mockedListProjects = vi.mocked(listProjects);

function detail(overrides: Partial<GroupDetail> = {}): GroupDetail {
  return {
    id: "group-1",
    name: "Platform",
    slug: "platform",
    description: "Owns the shared platform services.",
    parent_group_id: null,
    ancestors: [],
    child_group_count: 0,
    project_count: 0,
    member_count: 1,
    stats: {
      window_days: 30,
      subtree_scan_count: 4,
      subtree_approvals_processed_count: 2,
      subtree_new_member_count: 1,
    },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

function renderPage(groupId = "group-1") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/groups/${groupId}`]}>
        <Routes>
          <Route path="/groups/:groupId" element={<GroupDetailPage />} />
          <Route path="/groups/:groupId/parent-stub" element={<div />} />
          <Route path="/projects/new" element={<div data-testid="project-create-stub" />} />
          <Route path="/groups" element={<div data-testid="groups-list-stub" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("GroupDetailPage", () => {
  beforeEach(() => {
    mockedGetGroup.mockReset();
    mockedListGroups.mockReset();
    mockedGetMembers.mockReset();
    mockedListProjects.mockReset();
    mockedListGroups.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50 });
    mockedListProjects.mockResolvedValue({ items: [], total: 0, page: 1, size: 100 });
    useUIStore.setState({ activeTeamId: null });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders no page frame at all on the existence-hide 404", async () => {
    mockedGetGroup.mockRejectedValueOnce(
      new ProblemError("not found", {
        status: 404,
        title: "Not Found",
        detail: "not found",
        problem: null,
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("group-detail-not-found")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("group-detail-page")).not.toBeInTheDocument();
  });

  it("renders name, ancestor breadcrumb, and the 30-day subtree stats", async () => {
    mockedGetGroup.mockResolvedValueOnce(
      detail({
        ancestors: [
          { id: "root-id", name: "Engineering", slug: "engineering" },
        ],
      }),
    );
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("group-detail-name")).toHaveTextContent("Platform");
    });
    expect(screen.queryByTestId("group-detail-loading")).not.toBeInTheDocument();

    const segment = screen.getByTestId("group-detail-breadcrumb-segment");
    expect(segment).toHaveAttribute("data-group-name", "Engineering");
    expect(segment).toHaveAttribute("href", "/groups/root-id");

    const panel = screen.getByTestId("group-detail-stats-panel");
    expect(panel).toHaveAttribute("data-window-days", "30");
    expect(
      within(panel).getByTestId("group-detail-stat-subtree-scan-count"),
    ).toHaveAttribute("data-value", "4");
    expect(
      within(panel).getByTestId("group-detail-stat-subtree-approvals-processed"),
    ).toHaveAttribute("data-value", "2");
    expect(
      within(panel).getByTestId("group-detail-stat-subtree-new-members"),
    ).toHaveAttribute("data-value", "1");
  });

  it("New project sets uiStore.activeTeamId to this group and navigates to /projects/new", async () => {
    mockedGetGroup.mockResolvedValueOnce(detail());
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("group-detail-name")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("group-detail-new-project"));

    expect(useUIStore.getState().activeTeamId).toBe("group-1");
    await waitFor(() => {
      expect(screen.getByTestId("project-create-stub")).toBeInTheDocument();
    });
  });

  it("the Subgroups tab is the default and fetches direct children by parent_id", async () => {
    mockedGetGroup.mockResolvedValueOnce(detail());
    mockedListGroups.mockResolvedValueOnce({
      items: [
        {
          id: "sub-1",
          name: "Backend",
          slug: "backend",
          description: null,
          parent_group_id: "group-1",
          child_group_count: 0,
          project_count: 2,
          member_count: 3,
          updated_at: "2026-05-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 100,
    });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("group-detail-subgroup-row")).toHaveAttribute(
        "data-group-name",
        "Backend",
      );
    });
    expect(mockedListGroups).toHaveBeenCalledWith({
      parent_id: "group-1",
      page: 1,
      page_size: 100,
    });
  });

  it("the Members tab fetches direct/inherited members only once opened", async () => {
    mockedGetGroup.mockResolvedValueOnce(detail());
    mockedGetMembers.mockResolvedValueOnce({ direct: [], inherited: [] });
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("group-detail-name")).toBeInTheDocument();
    });
    expect(mockedGetMembers).not.toHaveBeenCalled();

    await userEvent.click(screen.getByTestId("group-detail-tab-members"));

    await waitFor(() => {
      expect(mockedGetMembers).toHaveBeenCalledWith("group-1");
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("group-members-inherited-empty-state"),
      ).toBeInTheDocument();
    });
  });

  it("the Projects tab lists this group's projects and refetches when 'include archived' toggles", async () => {
    mockedGetGroup.mockResolvedValueOnce(detail());
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("group-detail-name")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByTestId("group-detail-tab-projects"));
    await waitFor(() => {
      expect(mockedListProjects).toHaveBeenCalledWith(
        expect.objectContaining({ team_id: "group-1", include_archived: false }),
      );
    });

    await userEvent.click(
      screen.getByTestId("group-detail-projects-include-archived"),
    );
    await waitFor(() => {
      expect(mockedListProjects).toHaveBeenCalledWith(
        expect.objectContaining({ team_id: "group-1", include_archived: true }),
      );
    });
  });
});
