/**
 * GroupMembersPanel, unit tests, group-hierarchy Phase 4 PR 4-B.
 *
 * Covers the direct/inherited split the harness pins: a direct row carries
 * `data-email`/`data-role`, an inherited row additionally carries
 * `data-source-group-name`, and an empty inherited list renders the
 * dedicated empty state rather than nothing.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/groups/api/groupsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/features/groups/api/groupsApi")>();
  return { ...actual, getGroupMembers: vi.fn() };
});

import { getGroupMembers } from "@/features/groups/api/groupsApi";
import { GroupMembersPanel } from "@/features/groups/components/GroupMembersPanel";

const mockedMembers = vi.mocked(getGroupMembers);

function renderPanel(groupId = "group-1", active = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GroupMembersPanel groupId={groupId} active={active} />
    </QueryClientProvider>,
  );
}

describe("GroupMembersPanel", () => {
  beforeEach(() => {
    mockedMembers.mockReset();
  });

  it("renders direct and inherited rows with their source group attribution", async () => {
    mockedMembers.mockResolvedValueOnce({
      direct: [
        {
          user_id: "u-1",
          email: "dev@example.com",
          full_name: "Dev One",
          role: "developer",
          is_service_account: false,
        },
      ],
      inherited: [
        {
          user_id: "u-2",
          email: "admin@example.com",
          full_name: null,
          role: "group_admin",
          is_service_account: false,
          source_group_id: "ancestor-1",
          source_group_name: "Engineering",
        },
      ],
    });
    renderPanel();

    await waitFor(() => {
      expect(screen.getByTestId("group-members-direct-row")).toHaveAttribute(
        "data-email",
        "dev@example.com",
      );
    });
    expect(screen.getByTestId("group-members-direct-row")).toHaveAttribute(
      "data-role",
      "developer",
    );
    expect(screen.getByTestId("group-members-inherited-row")).toHaveAttribute(
      "data-source-group-name",
      "Engineering",
    );
    expect(
      screen.queryByTestId("group-members-inherited-empty-state"),
    ).not.toBeInTheDocument();
  });

  it("shows the inherited empty state when the cascade grants nothing", async () => {
    mockedMembers.mockResolvedValueOnce({ direct: [], inherited: [] });
    renderPanel();

    await waitFor(() => {
      expect(
        screen.getByTestId("group-members-inherited-empty-state"),
      ).toBeInTheDocument();
    });
    expect(screen.queryByTestId("group-members-inherited-row")).not.toBeInTheDocument();
    expect(screen.getByTestId("group-members-direct-empty")).toBeInTheDocument();
  });

  it("does not fetch while inactive", () => {
    renderPanel("group-1", false);
    expect(mockedMembers).not.toHaveBeenCalled();
  });
});
