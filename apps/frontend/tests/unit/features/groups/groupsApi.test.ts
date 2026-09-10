/**
 * groupsApi, unit tests, group-hierarchy Phase 4 PR 4-B.
 *
 * Mirrors the module-mock pattern used for other thin API wrappers
 * (`adminTeamsApi.test.ts`): stub `@/lib/api`'s `get`, assert the URL +
 * params each function sends and the shape it hands back untouched.
 */
import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { get: vi.fn() },
}));

import { api } from "@/lib/api";
import {
  getGroup,
  getGroupMembers,
  listGroups,
} from "@/features/groups/api/groupsApi";

const mockedGet = vi.mocked(api.get);

describe("groupsApi", () => {
  it("listGroups sends q/parent_id/page/page_size and returns the page envelope", async () => {
    const page = { items: [], total: 0, page: 1, page_size: 50 };
    mockedGet.mockResolvedValueOnce({ data: page });

    const result = await listGroups({
      q: "platform",
      parent_id: "parent-1",
      page: 2,
      page_size: 25,
    });

    expect(mockedGet).toHaveBeenCalledWith("/v1/groups", {
      params: { q: "platform", parent_id: "parent-1", page: 2, page_size: 25 },
    });
    expect(result).toBe(page);
  });

  it("listGroups defaults to an empty params object", async () => {
    mockedGet.mockResolvedValueOnce({
      data: { items: [], total: 0, page: 1, page_size: 50 },
    });
    await listGroups();
    expect(mockedGet).toHaveBeenCalledWith("/v1/groups", {
      params: {
        q: undefined,
        parent_id: undefined,
        page: undefined,
        page_size: undefined,
      },
    });
  });

  it("getGroup hits /v1/groups/{id}", async () => {
    const detail = { id: "g-1", name: "Platform" };
    mockedGet.mockResolvedValueOnce({ data: detail });
    const result = await getGroup("g-1");
    expect(mockedGet).toHaveBeenCalledWith("/v1/groups/g-1");
    expect(result).toBe(detail);
  });

  it("getGroupMembers hits /v1/groups/{id}/members", async () => {
    const members = { direct: [], inherited: [] };
    mockedGet.mockResolvedValueOnce({ data: members });
    const result = await getGroupMembers("g-1");
    expect(mockedGet).toHaveBeenCalledWith("/v1/groups/g-1/members");
    expect(result).toBe(members);
  });
});
