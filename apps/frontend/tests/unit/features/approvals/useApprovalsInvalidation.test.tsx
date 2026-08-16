/**
 * What an approval decision invalidates (C1).
 *
 * The sidebar badge reads the dashboard action queue, and that cache is not
 * something the approvals feature would otherwise think about. Without this
 * the count sat at its mount-time value while the user worked through the
 * queue in front of it.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ACTION_QUEUE_QUERY_KEY } from "@/features/dashboard/api/actionQueue";
import {
  useDeleteApproval,
  useTransitionApproval,
} from "@/features/approvals/useApprovals";

const transitionApproval = vi.fn();
const deleteApproval = vi.fn();

vi.mock("@/lib/approvalsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/approvalsApi")>();
  return {
    ...actual,
    transitionApproval: (...args: unknown[]) => transitionApproval(...args),
    deleteApproval: (...args: unknown[]) => deleteApproval(...args),
  };
});

let client: QueryClient;
let invalidated: unknown[][];

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** The keys any of these mutations asked React Query to invalidate. */
function invalidatedKeys(): string[] {
  return invalidated.map((key) => JSON.stringify(key));
}

describe("approval mutations invalidate the sidebar count", () => {
  beforeEach(() => {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    invalidated = [];
    const original = client.invalidateQueries.bind(client);
    vi.spyOn(client, "invalidateQueries").mockImplementation((filters) => {
      invalidated.push((filters?.queryKey ?? []) as unknown[]);
      return original(filters);
    });
    transitionApproval.mockReset();
    deleteApproval.mockReset();
  });

  it("refreshes the action queue after a decision", async () => {
    transitionApproval.mockResolvedValue({ id: "a-1", version: 2 });

    const { result } = renderHook(() => useTransitionApproval(), { wrapper });
    result.current.mutate({
      id: "a-1",
      action: "approved",
      etag: "1",
      decisionNote: "fine",
    });

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(invalidatedKeys()).toContain(JSON.stringify(["approvals"]));
    expect(invalidatedKeys()).toContain(JSON.stringify(ACTION_QUEUE_QUERY_KEY));
  });

  it("refreshes the action queue after a deletion", async () => {
    deleteApproval.mockResolvedValue(undefined);

    const { result } = renderHook(() => useDeleteApproval(), { wrapper });
    result.current.mutate("a-1");

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    expect(invalidatedKeys()).toContain(JSON.stringify(ACTION_QUEUE_QUERY_KEY));
  });
});
