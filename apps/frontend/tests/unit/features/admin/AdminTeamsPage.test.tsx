/**
 * AdminTeamsPage — unit tests covering list rendering, create flow, and the
 * row-click → drawer hand-off.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AdminTeamDetail,
  AdminTeamListItem,
  AdminTeamListResponse,
} from "@/features/admin/api/adminTeamsApi";
import { AdminTeamsPage } from "@/features/admin/teams/AdminTeamsPage";

vi.mock("@/features/admin/api/adminTeamsApi", async () => {
  return {
    listAdminTeams: vi.fn(),
    getAdminTeam: vi.fn(),
    createTeam: vi.fn(),
    updateTeam: vi.fn(),
    deleteTeam: vi.fn(),
    addTeamMember: vi.fn(),
    removeTeamMember: vi.fn(),
  };
});

import {
  createTeam,
  getAdminTeam,
  listAdminTeams,
} from "@/features/admin/api/adminTeamsApi";

const mockedList = vi.mocked(listAdminTeams);
const mockedGet = vi.mocked(getAdminTeam);
const mockedCreate = vi.mocked(createTeam);

function team(
  name: string,
  overrides: Partial<AdminTeamListItem> = {},
): AdminTeamListItem {
  return {
    id: overrides.id ?? `team-${name}`,
    name,
    slug: overrides.slug ?? name.toLowerCase(),
    description: overrides.description ?? null,
    member_count: overrides.member_count ?? 3,
    project_count: overrides.project_count ?? 1,
    created_at: overrides.created_at ?? "2026-04-01T00:00:00Z",
  };
}

function detailFromItem(t: AdminTeamListItem): AdminTeamDetail {
  return {
    id: t.id,
    name: t.name,
    slug: t.slug,
    description: t.description,
    project_count: t.project_count,
    members: [],
    created_at: t.created_at,
    updated_at: t.created_at,
  };
}

function listResponse(
  items: AdminTeamListItem[],
  total: number = items.length,
): AdminTeamListResponse {
  return { items, total, page: 1, page_size: 50 };
}

// MemoryRouter keeps its own history stack, so window.history is inert in
// these tests. This is how a test moves the URL without the page doing it.
function UrlProbe() {
  const navigate = useNavigate();
  return (
    <button
      data-testid="navigate-elsewhere"
      onClick={() => navigate("/admin/teams?search=platform")}
    />
  );
}

// B1: the filters live in the URL now, so the page needs a router.
function renderPage(url = "/admin/teams") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={[url]}>
      <QueryClientProvider client={client}>
        <AdminTeamsPage />
        <UrlProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("AdminTeamsPage", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedGet.mockReset();
    mockedCreate.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the team list once data resolves", async () => {
    mockedList.mockResolvedValue(listResponse([team("Core"), team("Platform")]));
    renderPage();
    await waitFor(() => {
      expect(screen.getAllByTestId("admin-teams-row")).toHaveLength(2);
    });
  });

  it("opens on the search and page the URL asked for (B1)", async () => {
    mockedList.mockResolvedValue(listResponse([team("Core")], 120));
    renderPage("/admin/teams?search=core&page=2&size=25");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({
      search: "core",
      page: 2,
      page_size: 25,
    });
    // And the field shows the term the list is filtered by.
    expect(screen.getByTestId("admin-teams-search")).toHaveValue("core");
  });

  it("keeps a deep-linked page when the search term has not moved (B1)", async () => {
    // The debounce writes the term on a timer. If it fired on mount it would
    // clear the page as a filter change.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    // A total that spans more than one page, or the clamp would rightly
    // snap page 2 back to 1 and mask what this test is about.
    mockedList.mockResolvedValue(listResponse([team("Core")], 120));
    renderPage("/admin/teams?search=core&page=2");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({
      search: "core",
      page: 2,
    });
  });

  it("snaps a page the list does not have back into range (B1)", async () => {
    // A bookmark can name page 5 of a list that now has one. Without this
    // the footer reads "Page 5 of 1" beside an empty table.
    mockedList.mockResolvedValue(listResponse([team("Core")]));
    renderPage("/admin/teams?page=5");

    await waitFor(() => {
      expect(mockedList.mock.calls.at(-1)?.[0]).toMatchObject({ page: 1 });
    });
  });

  it("keeps a trailing space the reader typed (B1)", async () => {
    // The URL holds the trimmed term. A field that follows the URL back
    // unconditionally swallows the space 300ms after it is typed, and the
    // next word joins the previous one.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockedList.mockResolvedValue(listResponse([]));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderPage();

    const field = screen.getByTestId("admin-teams-search");
    await user.type(field, "core ");
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(field).toHaveValue("core ");
  });

  it("follows the URL when the term changes from outside the field (B1)", async () => {
    // Back moves the URL. Without this the field keeps showing a term the
    // list is no longer filtered by.
    mockedList.mockResolvedValue(listResponse([]));
    renderPage("/admin/teams?search=core");
    expect(screen.getByTestId("admin-teams-search")).toHaveValue("core");

    // MemoryRouter keeps its own history stack, so window.history is inert
    // here. This is a navigation the page did not initiate, which is what
    // Back looks like from the field's side.
    await userEvent.click(screen.getByTestId("navigate-elsewhere"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-teams-search")).toHaveValue("platform");
    });
  });

  it("falls back to a sane page size for a value it does not offer (B1)", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderPage("/admin/teams?size=9999");

    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    expect(mockedList.mock.calls[0]?.[0]).toMatchObject({ page_size: 50 });
  });

  it("renders the empty state when no teams match", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-teams-empty")).toBeInTheDocument();
    });
  });

  it("toggles the create form and posts a new team", async () => {
    mockedList.mockResolvedValue(listResponse([]));
    const created = team("Core");
    mockedCreate.mockResolvedValue(detailFromItem(created));
    mockedGet.mockResolvedValue(detailFromItem(created));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-teams-empty")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-teams-new-button"));
    expect(screen.getByTestId("admin-teams-create-form")).toBeInTheDocument();
    await userEvent.type(screen.getByTestId("admin-teams-new-name"), "Core");
    await userEvent.type(screen.getByTestId("admin-teams-new-slug"), "core");
    await userEvent.click(screen.getByTestId("admin-teams-create-save"));
    await waitFor(() => {
      expect(mockedCreate).toHaveBeenCalledWith({
        name: "Core",
        slug: "core",
        description: null,
      });
    });
    // Drawer auto-opens for the freshly created team.
    await waitFor(() => {
      expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
    });
  });

  it("opens the team drawer on row click", async () => {
    const core = team("Core");
    mockedList.mockResolvedValue(listResponse([core]));
    mockedGet.mockResolvedValue(detailFromItem(core));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-teams-row")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-teams-row"));
    await waitFor(() => {
      expect(mockedGet).toHaveBeenCalledWith(core.id);
    });
  });

  it("renders an error alert on rejected list", async () => {
    mockedList.mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-teams-error")).toBeInTheDocument();
    });
  });
});
