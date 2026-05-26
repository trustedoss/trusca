/**
 * SeverityDistributionChart / LicenseDistributionChart — unit tests (PR #10).
 *
 * W4-B-prep extended these with the optional `onSegmentClick` prop: when
 * provided the segments + legend rows become buttons, when omitted the
 * existing static-div behavior is preserved.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LicenseDistributionChart } from "@/features/projects/components/LicenseDistributionChart";
import { SeverityDistributionChart } from "@/features/projects/components/SeverityDistributionChart";

describe("SeverityDistributionChart", () => {
  it("renders a bar segment per non-zero bucket and total in data attr", () => {
    render(
      <SeverityDistributionChart
        distribution={{ critical: 2, high: 3, medium: 1 }}
      />,
    );
    const root = screen.getByTestId("severity-distribution-chart");
    expect(root).toHaveAttribute("data-total", "6");
    expect(screen.getByTestId("severity-bar-critical")).toHaveAttribute(
      "data-count",
      "2",
    );
    expect(screen.getByTestId("severity-bar-high")).toHaveAttribute(
      "data-count",
      "3",
    );
    expect(screen.getByTestId("severity-bar-medium")).toHaveAttribute(
      "data-count",
      "1",
    );
    // Zero buckets render in the legend but not as a bar segment.
    expect(screen.queryByTestId("severity-bar-low")).not.toBeInTheDocument();
    expect(screen.getByTestId("severity-legend-low").textContent).toContain(
      "0",
    );
  });

  it("handles an empty distribution without crashing", () => {
    render(<SeverityDistributionChart distribution={{}} />);
    expect(screen.getByTestId("severity-distribution-chart")).toHaveAttribute(
      "data-total",
      "0",
    );
    // No bar segments, but the legend renders all six buckets at 0.
    expect(screen.getAllByTestId(/severity-legend-/)).toHaveLength(6);
  });
});

describe("LicenseDistributionChart", () => {
  it("renders a bar per non-zero category and shows totals", () => {
    render(
      <LicenseDistributionChart
        distribution={{ forbidden: 1, allowed: 4 }}
      />,
    );
    expect(screen.getByTestId("license-distribution-chart")).toHaveAttribute(
      "data-total",
      "5",
    );
    expect(screen.getByTestId("license-bar-forbidden")).toBeInTheDocument();
    expect(screen.getByTestId("license-bar-allowed")).toBeInTheDocument();
    expect(
      screen.queryByTestId("license-bar-conditional"),
    ).not.toBeInTheDocument();
  });
});

describe("Chart onSegmentClick — W4-B-prep", () => {
  it("renders severity segments + legend as static divs when callback omitted", () => {
    render(
      <SeverityDistributionChart
        distribution={{ critical: 2, high: 1 }}
      />,
    );
    // Non-interactive: not a button.
    expect(
      screen.getByTestId("severity-bar-critical").tagName.toLowerCase(),
    ).toBe("div");
    expect(
      screen.getByTestId("severity-legend-critical").tagName.toLowerCase(),
    ).toBe("li");
  });

  it("renders severity segments + legend as buttons when callback provided", () => {
    const handler = vi.fn();
    render(
      <SeverityDistributionChart
        distribution={{ critical: 2, high: 1 }}
        onSegmentClick={handler}
      />,
    );
    expect(
      screen.getByTestId("severity-bar-critical").tagName.toLowerCase(),
    ).toBe("button");
    // Legend container stays an <li>; the inner button gets the testId.
    expect(
      screen.getByTestId("severity-legend-critical").tagName.toLowerCase(),
    ).toBe("button");
  });

  it("forwards severity segment click to onSegmentClick with the bucket key", async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(
      <SeverityDistributionChart
        distribution={{ critical: 2, high: 1 }}
        onSegmentClick={handler}
      />,
    );
    await user.click(screen.getByTestId("severity-bar-critical"));
    await user.click(screen.getByTestId("severity-legend-high"));
    expect(handler).toHaveBeenCalledTimes(2);
    expect(handler).toHaveBeenNthCalledWith(1, "critical");
    expect(handler).toHaveBeenNthCalledWith(2, "high");
  });

  it("keeps zero-count legend rows non-interactive even with a callback", () => {
    const handler = vi.fn();
    render(
      <SeverityDistributionChart
        distribution={{ critical: 1 }}
        onSegmentClick={handler}
      />,
    );
    // The "low" bucket has count 0 — stays an <li>, not a button.
    expect(
      screen.getByTestId("severity-legend-low").tagName.toLowerCase(),
    ).toBe("li");
  });

  it("forwards license segment + legend clicks", async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(
      <LicenseDistributionChart
        distribution={{ forbidden: 1, allowed: 4 }}
        onSegmentClick={handler}
      />,
    );
    await user.click(screen.getByTestId("license-bar-forbidden"));
    await user.click(screen.getByTestId("license-legend-allowed"));
    expect(handler).toHaveBeenCalledTimes(2);
    expect(handler).toHaveBeenNthCalledWith(1, "forbidden");
    expect(handler).toHaveBeenNthCalledWith(2, "allowed");
  });
});
