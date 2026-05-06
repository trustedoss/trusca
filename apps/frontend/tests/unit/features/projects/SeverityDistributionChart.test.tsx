/**
 * SeverityDistributionChart / LicenseDistributionChart — unit tests (PR #10).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

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
