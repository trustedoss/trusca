/**
 * SeverityBadge / LicenseCategoryBadge — unit tests (PR #10).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LicenseCategoryBadge } from "@/features/projects/components/LicenseCategoryBadge";
import { SeverityBadge } from "@/features/projects/components/SeverityBadge";

describe("SeverityBadge", () => {
  it("renders critical with the localized text and a colored dot", () => {
    render(<SeverityBadge severity="critical" />);
    const badge = screen.getByTestId("severity-badge-critical");
    expect(badge).toHaveAttribute("data-severity", "critical");
    expect(badge.textContent).toContain("Critical");
    // Color is paired with the severity label, not used alone.
    expect(badge.querySelector("span[aria-hidden]")).toBeInTheDocument();
  });

  it("falls back to info dot for severity none", () => {
    render(<SeverityBadge severity="none" />);
    const badge = screen.getByTestId("severity-badge-none");
    expect(badge).toHaveAttribute("data-severity", "none");
    expect(badge.textContent).toContain("None");
  });
});

describe("LicenseCategoryBadge", () => {
  it("renders forbidden with critical tone and label", () => {
    render(<LicenseCategoryBadge category="forbidden" />);
    const badge = screen.getByTestId("license-category-badge-forbidden");
    expect(badge).toHaveAttribute("data-license-category", "forbidden");
    expect(badge.textContent).toContain("Forbidden");
  });

  it("renders allowed with the localized label", () => {
    render(<LicenseCategoryBadge category="allowed" />);
    expect(
      screen.getByTestId("license-category-badge-allowed").textContent,
    ).toContain("Allowed");
  });
});
