/**
 * AdminTeamDrawer — unit tests covering the edit form, add-member flow, and
 * delete-team confirmation.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  AdminTeamDetail,
  AdminTeamMember,
} from "@/features/admin/api/adminTeamsApi";
import { AdminTeamDrawer } from "@/features/admin/teams/AdminTeamDrawer";

vi.mock("@/features/admin/api/adminTeamsApi", async () => {
  return {
    getAdminTeam: vi.fn(),
    createTeam: vi.fn(),
    updateTeam: vi.fn(),
    deleteTeam: vi.fn(),
    addTeamMember: vi.fn(),
    removeTeamMember: vi.fn(),
  };
});

import {
  addTeamMember,
  deleteTeam,
  getAdminTeam,
  updateTeam,
} from "@/features/admin/api/adminTeamsApi";

const mockedGet = vi.mocked(getAdminTeam);
const mockedUpdate = vi.mocked(updateTeam);
const mockedAdd = vi.mocked(addTeamMember);
const mockedDelete = vi.mocked(deleteTeam);

function member(
  email: string,
  overrides: Partial<AdminTeamMember> = {},
): AdminTeamMember {
  return {
    user_id: overrides.user_id ?? `user-${email}`,
    email,
    full_name: overrides.full_name ?? null,
    role: overrides.role ?? "developer",
  };
}

function detail(overrides: Partial<AdminTeamDetail> = {}): AdminTeamDetail {
  return {
    id: "t1",
    name: "Core",
    slug: "core",
    description: "Core engineering",
    project_count: 4,
    members: [],
    created_at: "2026-04-01T00:00:00Z",
    updated_at: "2026-04-01T00:00:00Z",
    ...overrides,
  };
}

function renderDrawer(notify = vi.fn(), onDeleted = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return {
    notify,
    onDeleted,
    ...render(
      <QueryClientProvider client={client}>
        <AdminTeamDrawer
          open
          teamId="t1"
          onOpenChange={() => {}}
          notify={notify}
          onDeleted={onDeleted}
        />
      </QueryClientProvider>,
    ),
  };
}

describe("AdminTeamDrawer", () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedUpdate.mockReset();
    mockedAdd.mockReset();
    mockedDelete.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the team detail and members", async () => {
    mockedGet.mockResolvedValue(
      detail({ members: [member("alice@example.com")] }),
    );
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
    });
    expect(screen.getByText("Core")).toBeInTheDocument();
    expect(screen.getByTestId("admin-team-member-row")).toBeInTheDocument();
  });

  it("opens the edit form and patches the team", async () => {
    mockedGet.mockResolvedValue(detail());
    mockedUpdate.mockResolvedValue(detail({ name: "Renamed" }));
    const { notify } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-team-action-edit"));
    expect(screen.getByTestId("admin-team-edit-form")).toBeInTheDocument();
    const nameInput = screen.getByTestId("admin-team-name") as HTMLInputElement;
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, "Renamed");
    await userEvent.click(screen.getByTestId("admin-team-edit-save"));
    await waitFor(() => {
      expect(mockedUpdate).toHaveBeenCalledTimes(1);
    });
    const args = mockedUpdate.mock.calls[0];
    expect(args[0]).toBe("t1");
    expect((args[1] as { name?: string }).name).toBe("Renamed");
    expect(notify).toHaveBeenCalledWith(
      expect.any(String),
      "success",
      expect.any(String),
    );
  });

  it("opens the add-member form and posts a new membership", async () => {
    mockedGet.mockResolvedValue(detail());
    mockedAdd.mockResolvedValue(detail());
    renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-team-action-add-member"));
    expect(
      screen.getByTestId("admin-team-add-member-form"),
    ).toBeInTheDocument();
    await userEvent.type(
      screen.getByTestId("admin-team-member-user"),
      "user-uuid",
    );
    await userEvent.click(screen.getByTestId("admin-team-member-add-save"));
    await waitFor(() => {
      expect(mockedAdd).toHaveBeenCalledWith("t1", {
        user_id: "user-uuid",
        role: "developer",
      });
    });
  });

  it("requires confirmation before delete and propagates onDeleted", async () => {
    mockedGet.mockResolvedValue(detail());
    mockedDelete.mockResolvedValue();
    const { notify, onDeleted } = renderDrawer();
    await waitFor(() => {
      expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
    });
    await userEvent.click(screen.getByTestId("admin-team-action-delete"));
    expect(screen.getByTestId("admin-team-delete-confirm")).toBeInTheDocument();
    expect(mockedDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId("admin-team-delete-confirm-ok"));
    await waitFor(() => {
      // The wire wrapper takes the teamId positionally; the mutation hook
      // destructures the object form internally.
      expect(mockedDelete).toHaveBeenCalledWith("t1");
    });
    expect(onDeleted).toHaveBeenCalled();
    expect(notify).toHaveBeenCalledWith(
      expect.any(String),
      "success",
      expect.any(String),
    );
  });
});
