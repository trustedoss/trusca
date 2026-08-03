/**
 * LicenseKindBadge — unit tests (PR-A3).
 *
 * The badge distinguishes first-party `detected` (scancode) evidence from
 * dependency `declared` (cdxgen) evidence. The DoD requires that color is
 * never the only signal, so each kind must render a localized label too.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LicenseKindBadge } from "@/features/projects/components/LicenseKindBadge";

describe("LicenseKindBadge", () => {
  it("renders the detected kind with its localized label + stable data attr", () => {
    render(<LicenseKindBadge kind="detected" />);
    const badge = screen.getByTestId("license-kind-badge-detected");
    expect(badge).toHaveAttribute("data-license-kind", "detected");
    expect(badge).toHaveTextContent("Detected");
  });

  it("renders the declared kind distinctly from detected", () => {
    render(<LicenseKindBadge kind="declared" />);
    const badge = screen.getByTestId("license-kind-badge-declared");
    expect(badge).toHaveAttribute("data-license-kind", "declared");
    expect(badge).toHaveTextContent("Declared");
    // Declared (dependency) evidence does not carry the detected tint class.
    expect(screen.queryByTestId("license-kind-badge-detected")).toBeNull();
  });

  it("renders the concluded kind", () => {
    render(<LicenseKindBadge kind="concluded" />);
    expect(screen.getByTestId("license-kind-badge-concluded")).toHaveTextContent(
      "Concluded",
    );
  });
});
