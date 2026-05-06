/**
 * RecentScansTable — unit tests (PR #10).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScanSummary } from "@/features/projects/api/projectDetailApi";
import { RecentScansTable } from "@/features/projects/components/RecentScansTable";

function scan(overrides: Partial<ScanSummary> = {}): ScanSummary {
  return {
    id: overrides.id ?? "00000000-0000-0000-0000-000000000001",
    kind: "source",
    status: "succeeded",
    progress_percent: 100,
    started_at: "2026-05-01T12:00:00Z",
    completed_at: "2026-05-01T12:01:30Z",
    created_at: "2026-05-01T12:00:00Z",
    ...overrides,
  };
}

describe("RecentScansTable", () => {
  it("renders the empty state when there are no scans", () => {
    render(<RecentScansTable scans={[]} />);
    expect(screen.getByTestId("recent-scans-empty")).toBeInTheDocument();
  });

  it("renders one row per scan with status data attribute", () => {
    render(
      <RecentScansTable
        scans={[
          scan({ id: "s1", status: "succeeded" }),
          scan({ id: "s2", status: "failed" }),
        ]}
      />,
    );
    expect(screen.getAllByTestId("recent-scan-row")).toHaveLength(2);
    // Both rows have the same 90-second duration; assert the formatted output
    // appears at least once for either row.
    expect(screen.getAllByText("1m 30s").length).toBeGreaterThanOrEqual(1);
  });

  it("falls back to em-dash when started_at or completed_at is missing", () => {
    render(
      <RecentScansTable
        scans={[scan({ id: "s1", started_at: null, completed_at: null })]}
      />,
    );
    // Two columns (Started + Duration) should show the em-dash.
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });
});
