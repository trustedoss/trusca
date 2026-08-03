/**
 * LicenseColumnCell — W4-B-prep license-column split primitive.
 *
 * Confirms the cell renders the SPDX identifier + the policy badge as
 * separate visual surfaces, falls back to a dash when no license is
 * detected, and surfaces both facets through `data-` attributes for
 * downstream filters/tests.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LicenseColumnCell } from "@/features/projects/components/LicenseColumnCell";

describe("LicenseColumnCell", () => {
  it("renders SPDX text + LicenseCategoryBadge for an allowed license", () => {
    render(<LicenseColumnCell license="MIT" category="allowed" />);
    const cell = screen.getByTestId("license-column-cell");
    expect(cell).toHaveAttribute("data-license-spdx", "MIT");
    expect(cell).toHaveAttribute("data-license-category", "allowed");
    expect(cell.textContent).toContain("MIT");
    expect(
      screen.getByTestId("license-category-badge-allowed"),
    ).toBeInTheDocument();
  });

  it("renders a dash + unknown badge when license is null", () => {
    render(<LicenseColumnCell license={null} category="unknown" />);
    const cell = screen.getByTestId("license-column-cell");
    expect(cell).toHaveAttribute("data-license-spdx", "");
    expect(cell).toHaveAttribute("data-license-category", "unknown");
    expect(cell.textContent).toContain("—");
    expect(
      screen.getByTestId("license-category-badge-unknown"),
    ).toBeInTheDocument();
  });

  it("keeps SPDX + policy independent — same SPDX can be Forbidden", () => {
    // GPL-3.0 is forbidden in the policy catalog. The cell must surface both
    // facets so a user can tell *which* GPL is which.
    render(<LicenseColumnCell license="GPL-3.0" category="forbidden" />);
    const cell = screen.getByTestId("license-column-cell");
    expect(cell).toHaveAttribute("data-license-spdx", "GPL-3.0");
    expect(cell).toHaveAttribute("data-license-category", "forbidden");
    expect(cell.textContent).toContain("GPL-3.0");
    expect(
      screen.getByTestId("license-category-badge-forbidden"),
    ).toBeInTheDocument();
  });
});
