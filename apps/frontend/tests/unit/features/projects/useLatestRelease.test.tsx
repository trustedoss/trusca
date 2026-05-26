/**
 * useLatestRelease — hook unit tests (feature #28 Phase 1).
 *
 * The hook reads page 1 (size 1) of the newest-first releases list and returns
 * the first snapshot, or null when the project has no succeeded scan yet.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ReleaseSnapshot } from "@/features/projects/api/releasesApi";

vi.mock("@/features/projects/api/releasesApi", async () => {
  return { listProjectReleases: vi.fn() };
});

import { listProjectReleases } from "@/features/projects/api/releasesApi";
import { useLatestRelease } from "@/features/projects/api/useLatestRelease";

const mockedList = vi.mocked(listProjectReleases);

function snapshot(scanId: string): ReleaseSnapshot {
  return {
    scan_id: scanId,
    release: "v1.0.0",
    created_at: "2026-05-22T10:00:00Z",
    risk_score: 50,
    severity_summary: { critical: 0, high: 0, medium: 0, low: 0 },
    gate_status: "pass",
    component_count: 3,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

describe("useLatestRelease", () => {
  beforeEach(() => {
    mockedList.mockReset();
  });

  it("returns the newest snapshot (first list item)", async () => {
    mockedList.mockResolvedValueOnce({
      items: [snapshot("scan-latest")],
      total: 1,
      page: 1,
      size: 1,
    });
    const { result } = renderHook(() => useLatestRelease("proj-1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.scan_id).toBe("scan-latest");
    expect(mockedList).toHaveBeenCalledWith("proj-1", { page: 1, size: 1 });
  });

  it("returns null when there are no succeeded scans", async () => {
    mockedList.mockResolvedValueOnce({
      items: [],
      total: 0,
      page: 1,
      size: 1,
    });
    const { result } = renderHook(() => useLatestRelease("proj-1"), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("is disabled when projectId is undefined", () => {
    const { result } = renderHook(() => useLatestRelease(undefined), {
      wrapper,
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(mockedList).not.toHaveBeenCalled();
  });
});
