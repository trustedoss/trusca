/**
 * AdminTeamsPage — unit tests covering list rendering, create flow, and the
 * row-click → drawer hand-off.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

function listResponse(items: AdminTeamListItem[]): AdminTeamListResponse {
  return { items, total: items.length, page: 1, page_size: 50 };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AdminTeamsPage />
    </QueryClientProvider>,
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
