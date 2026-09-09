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
  AdminTeamListItem,
  AdminTeamMember,
} from "@/features/admin/api/adminTeamsApi";
import { AdminTeamDrawer } from "@/features/admin/teams/AdminTeamDrawer";
import { ProblemError } from "@/lib/problem";

vi.mock("@/features/admin/api/adminTeamsApi", async () => {
  return {
    getAdminTeam: vi.fn(),
    listAdminTeams: vi.fn(),
    createTeam: vi.fn(),
    updateTeam: vi.fn(),
    deleteTeam: vi.fn(),
    addTeamMember: vi.fn(),
    removeTeamMember: vi.fn(),
    reparentTeam: vi.fn(),
    createSubgroup: vi.fn(),
  };
});

import {
  addTeamMember,
  createSubgroup,
  deleteTeam,
  getAdminTeam,
  listAdminTeams,
  reparentTeam,
  updateTeam,
} from "@/features/admin/api/adminTeamsApi";

const mockedGet = vi.mocked(getAdminTeam);
const mockedList = vi.mocked(listAdminTeams);
const mockedUpdate = vi.mocked(updateTeam);
const mockedAdd = vi.mocked(addTeamMember);
const mockedDelete = vi.mocked(deleteTeam);
const mockedReparent = vi.mocked(reparentTeam);
const mockedCreateSubgroup = vi.mocked(createSubgroup);

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
    organization_id: "org-1",
    parent_group_id: null,
    ...overrides,
  };
}

function listItem(
  id: string,
  name: string,
  overrides: Partial<AdminTeamListItem> = {},
): AdminTeamListItem {
  return {
    id,
    name,
    slug: overrides.slug ?? name.toLowerCase(),
    description: overrides.description ?? null,
    member_count: overrides.member_count ?? 0,
    project_count: overrides.project_count ?? 0,
    created_at: overrides.created_at ?? "2026-04-01T00:00:00Z",
    organization_id: overrides.organization_id ?? "org-1",
    parent_group_id:
      overrides.parent_group_id === undefined
        ? null
        : overrides.parent_group_id,
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
    mockedList.mockReset();
    // The move picker + parent badge both read this list; default it to
    // empty so tests that don't care about hierarchy don't need to stub it.
    mockedList.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 200 });
    mockedUpdate.mockReset();
    mockedAdd.mockReset();
    mockedDelete.mockReset();
    mockedReparent.mockReset();
    mockedCreateSubgroup.mockReset();
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

  // group-hierarchy Phase 5 PR 5-B.
  describe("group hierarchy", () => {
    it("shows the root label with an empty data-parent-name for a root group", async () => {
      mockedGet.mockResolvedValue(detail({ parent_group_id: null }));
      renderDrawer();
      await waitFor(() => {
        expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
      });
      const badge = screen.getByTestId("admin-team-drawer-parent");
      expect(badge).toHaveAttribute("data-parent-name", "");
    });

    it("resolves and shows the parent group's name via the flat team list", async () => {
      mockedGet.mockResolvedValue(
        detail({ parent_group_id: "t-parent", organization_id: "org-1" }),
      );
      mockedList.mockResolvedValue({
        items: [listItem("t-parent", "Umbrella", { organization_id: "org-1" })],
        total: 1,
        page: 1,
        page_size: 200,
      });
      renderDrawer();
      await waitFor(() => {
        const badge = screen.getByTestId("admin-team-drawer-parent");
        expect(badge).toHaveAttribute("data-parent-name", "Umbrella");
      });
    });

    it("filters the move picker to same-organization groups outside the open group's own subtree", async () => {
      mockedGet.mockResolvedValue(
        detail({ id: "t1", organization_id: "org-1", parent_group_id: null }),
      );
      mockedList.mockResolvedValue({
        items: [
          listItem("t1", "Core", { organization_id: "org-1" }),
          listItem("t1-child", "Core Child", {
            organization_id: "org-1",
            parent_group_id: "t1",
          }),
          listItem("t1-grandchild", "Core Grandchild", {
            organization_id: "org-1",
            parent_group_id: "t1-child",
          }),
          listItem("t3", "Platform", { organization_id: "org-1" }),
          listItem("t4", "Other Org Root", { organization_id: "org-2" }),
        ],
        total: 5,
        page: 1,
        page_size: 200,
      });
      renderDrawer();
      await waitFor(() => {
        expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
      });
      await userEvent.click(screen.getByTestId("admin-team-action-move"));
      const picker = await screen.findByTestId("admin-team-move-target");
      await waitFor(() => {
        const names = Array.from(
          picker.querySelectorAll("option[data-group-name]"),
        ).map((o) => o.getAttribute("data-group-name"));
        expect(names).toEqual(["Platform"]);
      });
      // The synthetic root option is always present alongside the filtered
      // candidates.
      expect(
        screen.getByTestId("admin-team-move-target-option-root"),
      ).toBeInTheDocument();
    });

    it("moves the group to root via the explicit root option", async () => {
      mockedGet.mockResolvedValue(detail({ id: "t1" }));
      mockedReparent.mockResolvedValue(detail({ id: "t1", parent_group_id: null }));
      renderDrawer();
      await waitFor(() => {
        expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
      });
      await userEvent.click(screen.getByTestId("admin-team-action-move"));
      await screen.findByTestId("admin-team-move-form");
      await userEvent.click(screen.getByTestId("admin-team-move-save"));
      await waitFor(() => {
        expect(mockedReparent).toHaveBeenCalledWith("t1", null);
      });
    });

    it("moves the group under the selected candidate", async () => {
      mockedGet.mockResolvedValue(
        detail({ id: "t1", organization_id: "org-1" }),
      );
      mockedList.mockResolvedValue({
        items: [
          listItem("t1", "Core", { organization_id: "org-1" }),
          listItem("t3", "Platform", { organization_id: "org-1" }),
        ],
        total: 2,
        page: 1,
        page_size: 200,
      });
      mockedReparent.mockResolvedValue(
        detail({ id: "t1", parent_group_id: "t3" }),
      );
      const { notify } = renderDrawer();
      await waitFor(() => {
        expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
      });
      await userEvent.click(screen.getByTestId("admin-team-action-move"));
      const picker = await screen.findByTestId("admin-team-move-target");
      await waitFor(() => {
        expect(
          picker.querySelector('option[data-group-name="Platform"]'),
        ).toBeInTheDocument();
      });
      await userEvent.selectOptions(picker, "t3");
      await userEvent.click(screen.getByTestId("admin-team-move-save"));
      await waitFor(() => {
        expect(mockedReparent).toHaveBeenCalledWith("t1", "t3");
      });
      expect(notify).toHaveBeenCalledWith(
        expect.any(String),
        "success",
        "moved",
      );
    });

    it("surfaces a cycle rejection distinctly from a slug conflict (both are 409)", async () => {
      // Security review finding (Low): GroupCycleDetected and
      // GroupSlugConflict are BOTH 409, and adminErrorMessage's fallback for
      // an unrecognised 409 is "slug_conflict" -- so this pins that the
      // extension map's cycle_detected entry is actually checked, not just
      // present in the source. Without this test, a future reordering of
      // EXTENSION_KEY_MAP, or a typo in the extension field name on either
      // side of the frontend/backend boundary, would silently regress to
      // showing "a group with that slug already exists" for what is really
      // a rejected cycle -- wrong guidance, since renaming the slug cannot
      // fix a cycle.
      mockedGet.mockResolvedValue(detail({ id: "t1" }));
      mockedList.mockResolvedValue({
        items: [listItem("t1", "Core"), listItem("t3", "Platform")],
        total: 2,
        page: 1,
        page_size: 200,
      });
      mockedReparent.mockRejectedValue(
        new ProblemError("Group Cycle Detected", {
          status: 409,
          title: "Group Cycle Detected",
          detail: "moving t1 under t3 would make it its own ancestor",
          problem: {
            type: "about:blank",
            title: "Group Cycle Detected",
            status: 409,
            detail: "moving t1 under t3 would make it its own ancestor",
            cycle_detected: true,
          },
        }),
      );
      const { notify } = renderDrawer();
      await waitFor(() => {
        expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
      });
      await userEvent.click(screen.getByTestId("admin-team-action-move"));
      const picker = await screen.findByTestId("admin-team-move-target");
      await waitFor(() => {
        expect(
          picker.querySelector('option[data-group-name="Platform"]'),
        ).toBeInTheDocument();
      });
      await userEvent.selectOptions(picker, "t3");
      await userEvent.click(screen.getByTestId("admin-team-move-save"));
      await waitFor(() => {
        expect(mockedReparent).toHaveBeenCalledWith("t1", "t3");
      });
      expect(notify).toHaveBeenCalledWith(
        expect.any(String),
        "error",
        "cycle_detected",
      );
    });

    it("opens the add-subgroup form and posts the new group under the open team", async () => {
      mockedGet.mockResolvedValue(detail({ id: "t1" }));
      mockedCreateSubgroup.mockResolvedValue(
        detail({ id: "t-child", name: "Child" }),
      );
      const { notify } = renderDrawer();
      await waitFor(() => {
        expect(screen.getByTestId("admin-team-drawer")).toBeInTheDocument();
      });
      await userEvent.click(
        screen.getByTestId("admin-team-action-add-subgroup"),
      );
      expect(
        screen.getByTestId("admin-team-subgroup-form"),
      ).toBeInTheDocument();
      await userEvent.type(
        screen.getByTestId("admin-team-subgroup-name"),
        "Child",
      );
      await userEvent.type(
        screen.getByTestId("admin-team-subgroup-slug"),
        "child",
      );
      await userEvent.click(screen.getByTestId("admin-team-subgroup-save"));
      await waitFor(() => {
        expect(mockedCreateSubgroup).toHaveBeenCalledWith("t1", {
          name: "Child",
          slug: "child",
          description: null,
        });
      });
      expect(notify).toHaveBeenCalledWith(
        expect.any(String),
        "success",
        "subgroup_created",
      );
      expect(
        screen.queryByTestId("admin-team-subgroup-form"),
      ).not.toBeInTheDocument();
    });
  });
});
