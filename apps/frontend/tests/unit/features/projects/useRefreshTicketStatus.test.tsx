/**
 * useRefreshTicketStatus - unit tests (#385).
 *
 * Mirrors `useUpdateFindingAssignment.test.tsx`'s "writes the server's
 * payload into the detail cache" case: the assertion that matters is that
 * the query the drawer reads actually carries the new answer, not merely
 * that the mutation function was called.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useRefreshTicketStatus } from "@/features/projects/api/useRefreshTicketStatus";
import { vulnerabilityKey } from "@/features/projects/api/useVulnerability";
import type { VulnerabilityDetail } from "@/features/projects/api/vulnerabilitiesApi";

vi.mock("@/features/projects/api/vulnerabilitiesApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/vulnerabilitiesApi")
  >("@/features/projects/api/vulnerabilitiesApi");
  return { ...actual, refreshTicketStatus: vi.fn() };
});

import { refreshTicketStatus } from "@/features/projects/api/vulnerabilitiesApi";

const mockedRefresh = vi.mocked(refreshTicketStatus);

const FINDING_ID = "22222222-2222-2222-2222-222222222222";

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

function seededDetail(): VulnerabilityDetail {
  return {
    id: FINDING_ID,
    ticket_url: "https://example.atlassian.net/browse/PROJ-1",
    ticket_status: null,
    ticket_resolved: null,
    ticket_checked_at: null,
    ticket_check_error: null,
  } as never;
}

describe("useRefreshTicketStatus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("writes the refreshed answer into the detail cache", async () => {
    mockedRefresh.mockResolvedValue({
      finding_id: FINDING_ID,
      ticket_status: "Done",
      ticket_resolved: true,
      ticket_checked_at: "2026-09-10T00:00:00Z",
      ticket_check_error: null,
    });

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    client.setQueryData(vulnerabilityKey(FINDING_ID), seededDetail());

    const { result } = renderHook(() => useRefreshTicketStatus(), {
      wrapper: wrapper(client),
    });

    result.current.mutate(FINDING_ID);

    await waitFor(() =>
      expect(
        client.getQueryData<VulnerabilityDetail>(vulnerabilityKey(FINDING_ID))
          ?.ticket_status,
      ).toBe("Done"),
    );
    const updated = client.getQueryData<VulnerabilityDetail>(
      vulnerabilityKey(FINDING_ID),
    );
    expect(updated?.ticket_resolved).toBe(true);
    expect(updated?.ticket_checked_at).toBe("2026-09-10T00:00:00Z");
    expect(updated?.ticket_check_error).toBeNull();
    // Fields the mutation does not touch survive the write.
    expect(updated?.ticket_url).toBe(
      "https://example.atlassian.net/browse/PROJ-1",
    );
  });

  it("records a failed check as data, without clearing an unrelated field", async () => {
    mockedRefresh.mockResolvedValue({
      finding_id: FINDING_ID,
      ticket_status: null,
      ticket_resolved: null,
      ticket_checked_at: "2026-09-10T00:00:00Z",
      ticket_check_error: "no ticket-tracker credential configured for 'example.atlassian.net'",
    });

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    client.setQueryData(vulnerabilityKey(FINDING_ID), seededDetail());

    const { result } = renderHook(() => useRefreshTicketStatus(), {
      wrapper: wrapper(client),
    });

    result.current.mutate(FINDING_ID);

    await waitFor(() =>
      expect(
        client.getQueryData<VulnerabilityDetail>(vulnerabilityKey(FINDING_ID))
          ?.ticket_check_error,
      ).not.toBeNull(),
    );
    expect(
      client.getQueryData<VulnerabilityDetail>(vulnerabilityKey(FINDING_ID))
        ?.ticket_url,
    ).toBe("https://example.atlassian.net/browse/PROJ-1");
  });

  it("does nothing to the cache when the finding was never loaded into it", async () => {
    // Guards the `previous === undefined` branch: a mutation racing ahead of
    // the detail query populating must not seed a partial, wrong-shaped
    // cache entry for a query nobody has fetched yet.
    mockedRefresh.mockResolvedValue({
      finding_id: FINDING_ID,
      ticket_status: "Done",
      ticket_resolved: true,
      ticket_checked_at: "2026-09-10T00:00:00Z",
      ticket_check_error: null,
    });

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });

    const { result } = renderHook(() => useRefreshTicketStatus(), {
      wrapper: wrapper(client),
    });

    result.current.mutate(FINDING_ID);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(
      client.getQueryData<VulnerabilityDetail>(vulnerabilityKey(FINDING_ID)),
    ).toBeUndefined();
  });
});
