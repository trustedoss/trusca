/**
 * Snapshot anchoring (`?scan=`) — unit tests (feature #28 Phase 1).
 *
 * Verifies that the detail-tab data paths thread a pinned `scanId` down to the
 * wire layer as the `scan_id` query param, and omit it when not pinned (the
 * latest-succeeded default is unchanged). We render the tab components with a
 * mocked wire layer and assert the params the wire fn received — same style as
 * LicensesTab.test.tsx.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type React from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { VulnerabilityListResponse } from "@/features/projects/api/vulnerabilitiesApi";

vi.mock("@/features/projects/api/vulnerabilitiesApi", async () => {
  const actual = await vi.importActual<
    typeof import("@/features/projects/api/vulnerabilitiesApi")
  >("@/features/projects/api/vulnerabilitiesApi");
  return {
    ...actual,
    listProjectVulnerabilities: vi.fn(),
    getVulnerabilityFinding: vi.fn(),
  };
});

// Stub Virtuoso so all rows mount in jsdom.
vi.mock("react-virtuoso", () => ({
  Virtuoso: <T,>({
    data,
    itemContent,
  }: {
    data: T[];
    itemContent: (index: number, item: T) => React.ReactNode;
  }) => (
    <div data-testid="virtuoso-stub">
      {data.map((item, idx) => (
        <div key={idx}>{itemContent(idx, item)}</div>
      ))}
    </div>
  ),
}));

// The vuln tab also fetches the overview (for the team-scoped role) + report;
// stub those so the test stays focused on the list params.
vi.mock("@/features/projects/api/useProjectOverview", () => ({
  useProjectOverview: () => ({ data: undefined, isLoading: false }),
  projectOverviewKey: (id: string) => ["projects", id, "overview"],
}));
vi.mock("@/features/projects/api/useVulnReport", () => ({
  useVulnReport: () => ({
    download: vi.fn(),
    isLoading: false,
    error: null,
  }),
}));

import { listProjectVulnerabilities } from "@/features/projects/api/vulnerabilitiesApi";
import { VulnerabilitiesTab } from "@/features/projects/components/VulnerabilitiesTab";

const mockedList = vi.mocked(listProjectVulnerabilities);

function emptyResponse(): VulnerabilityListResponse {
  return { items: [], total: 0, limit: 100, offset: 0 };
}

function renderTab(props: { scanId?: string; readOnly?: boolean } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/projects/proj-1"]}>
        <VulnerabilitiesTab projectId="proj-1" {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("snapshot scan_id anchoring", () => {
  beforeEach(() => {
    mockedList.mockReset();
    mockedList.mockResolvedValue(emptyResponse());
  });

  it("sends scan_id when scanId is set", async () => {
    renderTab({ scanId: "scan-historical-1" });
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalledWith(
        "proj-1",
        expect.objectContaining({ scanId: "scan-historical-1" }),
      );
    });
  });

  it("omits scan_id (undefined) when scanId is not set", async () => {
    renderTab();
    await waitFor(() => {
      expect(mockedList).toHaveBeenCalled();
    });
    const lastCall = mockedList.mock.calls.at(-1);
    expect(lastCall?.[1]?.scanId).toBeUndefined();
  });

  it("disables the VEX import control in read-only historical mode", async () => {
    renderTab({ scanId: "scan-historical-1", readOnly: true });
    await waitFor(() => {
      expect(screen.getByTestId("vex-import-open")).toBeInTheDocument();
    });
    const importBtn = screen.getByTestId("vex-import-open");
    expect(importBtn).toBeDisabled();
    expect(importBtn).toHaveAttribute("data-readonly-gated", "true");
  });
});
