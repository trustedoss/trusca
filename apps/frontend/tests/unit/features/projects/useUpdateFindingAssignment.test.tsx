/**
 * useUpdateFindingAssignment — unit tests (ER28b).
 *
 * The assertion that matters here is that the LIST ends up showing the new
 * value. "invalidateQueries was called" passes with a key that matches
 * nothing, which is the bug this hook shipped once: the mutation succeeded,
 * the drawer refreshed from its own cache write, and only the table stayed
 * stale, so the list looked wrong rather than the invalidation looking wrong.
 *
 * So these drive a real QueryClient with a real list query mounted, and check
 * the query's data after the save.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useUpdateFindingAssignment } from "@/features/projects/api/useUpdateFindingAssignment";
import {
  useVulnerabilities,
  type VulnerabilitiesQueryFilters,
} from "@/features/projects/api/useVulnerabilities";
import { vulnerabilityKey } from "@/features/projects/api/useVulnerability";
import type { VulnerabilityDetail } from "@/features/projects/api/vulnerabilitiesApi";

vi.mock("@/features/projects/api/vulnerabilitiesApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/vulnerabilitiesApi")
  >("@/features/projects/api/vulnerabilitiesApi");
  return {
    ...actual,
    updateFindingAssignment: vi.fn(),
    listProjectVulnerabilities: vi.fn(),
  };
});

import {
  listProjectVulnerabilities,
  updateFindingAssignment,
} from "@/features/projects/api/vulnerabilitiesApi";

const mockedPatch = vi.mocked(updateFindingAssignment);
const mockedList = vi.mocked(listProjectVulnerabilities);

const PROJECT_ID = "11111111-1111-1111-1111-111111111111";
const FINDING_ID = "22222222-2222-2222-2222-222222222222";
const USER_ID = "33333333-3333-3333-3333-333333333333";

const FILTERS: VulnerabilitiesQueryFilters = {
  search: "",
  severity: [],
  status: [],
  sort: "severity",
  order: "desc",
  min_epss: null,
  reachable: null,
  sla: null,
  assignee: null,
  license_category: [],
  limit: 50,
};

function listPage(assigneeUserId: string | null) {
  return {
    items: [
      {
        id: FINDING_ID,
        assignee_user_id: assigneeUserId,
        assignee_is_active: assigneeUserId === null ? null : true,
      },
    ],
    total: 1,
    severity_distribution: {},
  } as never;
}

function detailAfterSave(): VulnerabilityDetail {
  return {
    id: FINDING_ID,
    assignee_user_id: USER_ID,
    assignee_is_active: true,
  } as never;
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useUpdateFindingAssignment", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("makes the list show the new owner, not merely call invalidate", async () => {
    // Unassigned first, owned by the user on every refetch after the save.
    mockedList.mockResolvedValueOnce(listPage(null));
    mockedList.mockResolvedValue(listPage(USER_ID));
    mockedPatch.mockResolvedValue(detailAfterSave());

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = wrapper(client);

    const list = renderHook(() => useVulnerabilities(PROJECT_ID, FILTERS), {
      wrapper: wrap,
    });
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true));
    // The row starts unassigned, so the assertion below is a real change
    // rather than a value that was already there.
    expect(
      list.result.current.data?.pages[0].items[0].assignee_user_id,
    ).toBeNull();

    const save = renderHook(() => useUpdateFindingAssignment(PROJECT_ID), {
      wrapper: wrap,
    });
    save.result.current.mutate({
      findingId: FINDING_ID,
      body: { assignee_user_id: USER_ID },
    });

    await waitFor(() =>
      expect(
        list.result.current.data?.pages[0].items[0].assignee_user_id,
      ).toBe(USER_ID),
    );
  });

  it("refreshes a list that is FILTERED, not only the unfiltered one", async () => {
    // The invalidation is by prefix, so every filter combination should
    // refresh. A test with one unfiltered list passes even when only a single
    // exact key matches, which would leave every filtered view stale.
    //
    // The case that bites: while filtered to "mine", unassigning a finding
    // must remove the row. If the filtered list does not refresh, the finding
    // just released still sits in the user's own queue and reads as though
    // the unassign did not take.
    const mineFilters = { ...FILTERS, assignee: "me" as const };
    mockedList.mockResolvedValueOnce(listPage(USER_ID)); // before: it is mine
    mockedList.mockResolvedValue({
      items: [],
      total: 0,
      severity_distribution: {},
    } as never); // after: released, so it leaves the "mine" list
    mockedPatch.mockResolvedValue({
      id: FINDING_ID,
      assignee_user_id: null,
      assignee_is_active: null,
    } as never);

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = wrapper(client);

    const list = renderHook(() => useVulnerabilities(PROJECT_ID, mineFilters), {
      wrapper: wrap,
    });
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true));
    expect(list.result.current.data?.pages[0].items).toHaveLength(1);

    const save = renderHook(() => useUpdateFindingAssignment(PROJECT_ID), {
      wrapper: wrap,
    });
    save.result.current.mutate({
      findingId: FINDING_ID,
      body: { assignee_user_id: null },
    });

    await waitFor(() =>
      expect(list.result.current.data?.pages[0].items).toHaveLength(0),
    );
  });

  it("refreshes the unassigned list when a finding is taken", async () => {
    // The mirror of the case above, and the one that matters for takeover:
    // claiming a finding must remove it from what nobody owns.
    const unassignedFilters = { ...FILTERS, assignee: "unassigned" as const };
    mockedList.mockResolvedValueOnce(listPage(null));
    mockedList.mockResolvedValue({
      items: [],
      total: 0,
      severity_distribution: {},
    } as never);
    mockedPatch.mockResolvedValue(detailAfterSave());

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = wrapper(client);

    const list = renderHook(
      () => useVulnerabilities(PROJECT_ID, unassignedFilters),
      { wrapper: wrap },
    );
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true));
    expect(list.result.current.data?.pages[0].items).toHaveLength(1);

    const save = renderHook(() => useUpdateFindingAssignment(PROJECT_ID), {
      wrapper: wrap,
    });
    save.result.current.mutate({
      findingId: FINDING_ID,
      body: { assignee_user_id: USER_ID },
    });

    await waitFor(() =>
      expect(list.result.current.data?.pages[0].items).toHaveLength(0),
    );
  });

  it("flips the row from cannot-act to yours after a takeover", async () => {
    // ER54 closes on both halves here. Rendering the blocked state is one; the
    // list actually moving off it once somebody takes the work is the other.
    // A takeover that left the row reading "owner cannot act" would look like
    // the takeover had not happened.
    mockedList.mockResolvedValueOnce({
      items: [
        {
          id: FINDING_ID,
          assignee_user_id: "99999999-9999-9999-9999-999999999999",
          assignee_is_active: false,
        },
      ],
      total: 1,
      severity_distribution: {},
    } as never);
    mockedList.mockResolvedValue(listPage(USER_ID));
    mockedPatch.mockResolvedValue(detailAfterSave());

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const wrap = wrapper(client);

    const list = renderHook(() => useVulnerabilities(PROJECT_ID, FILTERS), {
      wrapper: wrap,
    });
    await waitFor(() => expect(list.result.current.isSuccess).toBe(true));
    expect(
      list.result.current.data?.pages[0].items[0].assignee_is_active,
    ).toBe(false);

    const save = renderHook(() => useUpdateFindingAssignment(PROJECT_ID), {
      wrapper: wrap,
    });
    save.result.current.mutate({
      findingId: FINDING_ID,
      body: { assignee_user_id: USER_ID },
    });

    await waitFor(() => {
      const row = list.result.current.data?.pages[0].items[0];
      expect(row?.assignee_user_id).toBe(USER_ID);
      expect(row?.assignee_is_active).toBe(true);
    });
  });

  it("writes the server's payload into the detail cache", async () => {
    mockedList.mockResolvedValue(listPage(null));
    mockedPatch.mockResolvedValue(detailAfterSave());

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const { result } = renderHook(
      () => useUpdateFindingAssignment(PROJECT_ID),
      { wrapper: wrapper(client) },
    );

    result.current.mutate({
      findingId: FINDING_ID,
      body: { assignee_user_id: USER_ID },
    });

    await waitFor(() =>
      expect(
        client.getQueryData<VulnerabilityDetail>(vulnerabilityKey(FINDING_ID))
          ?.assignee_user_id,
      ).toBe(USER_ID),
    );
  });
});
