/**
 * The recent-scans empty state (C3).
 *
 * The end of the first-scan path: a project is registered and nothing has
 * looked at it. It used to be a line of muted text with no way forward, and
 * the card above it printed a subtitle promising "Last five scans for this
 * project" over nothing.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecentScansTable } from "@/features/projects/components/RecentScansTable";
import type { ScanSummary } from "@/features/projects/api/projectDetailApi";

function scan(overrides: Partial<ScanSummary> = {}): ScanSummary {
  return {
    id: "s-1",
    status: "succeeded",
    started_at: "2026-08-01T00:00:00Z",
    completed_at: "2026-08-01T00:01:00Z",
    ...overrides,
  } as ScanSummary;
}

describe("RecentScansTable empty state", () => {
  it("offers the scan that would fill it", async () => {
    const onScan = vi.fn();
    const user = userEvent.setup();

    render(<RecentScansTable scans={[]} onScan={onScan} />);

    const empty = screen.getByTestId("recent-scans-empty");
    // The description is the part that was missing: "no scans" alone reads as
    // a fact about the project rather than as the one step still to take.
    expect(empty.textContent).toContain("has not been scanned yet");

    await user.click(screen.getByTestId("recent-scans-scan"));
    expect(onScan).toHaveBeenCalledOnce();
  });

  it("offers nothing when the reader cannot scan", async () => {
    // The prop is omitted rather than the button disabled: on a demo
    // deployment, or a historical snapshot, a button that refuses is worse
    // than no button.
    render(<RecentScansTable scans={[]} />);

    expect(screen.getByTestId("recent-scans-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("recent-scans-scan")).toBeNull();
  });

  it("shows rows, not the empty state, once a scan exists", () => {
    render(<RecentScansTable scans={[scan()]} onScan={vi.fn()} />);

    expect(screen.queryByTestId("recent-scans-empty")).toBeNull();
    expect(screen.queryByTestId("recent-scans-scan")).toBeNull();
  });
});
