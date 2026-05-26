/**
 * RecentScansTable — unit tests (PR #10).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

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

  it("keeps rows read-only (no button role) when onSelectScan is omitted", () => {
    render(<RecentScansTable scans={[scan()]} />);
    const row = screen.getByTestId("recent-scan-row");
    expect(row).not.toHaveAttribute("role", "button");
    expect(row).not.toHaveAttribute("tabindex");
  });

  it("makes each row an activatable control when onSelectScan is supplied", () => {
    render(<RecentScansTable scans={[scan()]} onSelectScan={vi.fn()} />);
    const row = screen.getByTestId("recent-scan-row");
    expect(row).toHaveAttribute("role", "button");
    expect(row).toHaveAttribute("tabindex", "0");
    expect(row).toHaveAttribute("aria-label");
  });

  it("invokes onSelectScan with the scan on click", () => {
    const onSelectScan = vi.fn();
    const target = scan({ id: "scan-42", status: "running" });
    render(<RecentScansTable scans={[target]} onSelectScan={onSelectScan} />);
    fireEvent.click(screen.getByTestId("recent-scan-row"));
    expect(onSelectScan).toHaveBeenCalledTimes(1);
    expect(onSelectScan).toHaveBeenCalledWith(target);
  });

  it("invokes onSelectScan on Enter and Space, but ignores other keys", () => {
    const onSelectScan = vi.fn();
    const target = scan({ id: "scan-7", status: "queued" });
    render(<RecentScansTable scans={[target]} onSelectScan={onSelectScan} />);
    const row = screen.getByTestId("recent-scan-row");
    fireEvent.keyDown(row, { key: "Tab" });
    expect(onSelectScan).not.toHaveBeenCalled();
    fireEvent.keyDown(row, { key: "Enter" });
    fireEvent.keyDown(row, { key: " " });
    expect(onSelectScan).toHaveBeenCalledTimes(2);
    expect(onSelectScan).toHaveBeenNthCalledWith(1, target);
    expect(onSelectScan).toHaveBeenNthCalledWith(2, target);
  });
});
