/**
 * KevBadge — unit tests (CISA KEV signal).
 *
 * Validates the render contract:
 *   - kev=true          → "KEV" badge with a colored dot + text label.
 *   - kev=false / undef → nothing (absence reads as "not listed").
 *   - dueDate           → folded into the title tooltip; rendered inline only
 *                         when `showDueDate` (drawer surface).
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KevBadge } from "@/features/projects/components/KevBadge";

describe("KevBadge", () => {
  it("renders a KEV badge with a dot + text label for kev=true", () => {
    render(<KevBadge kev={true} />);
    const badge = screen.getByTestId("kev-badge");
    // Color is paired with the literal "KEV" label + dot, never color-only.
    expect(badge.textContent).toContain("KEV");
    expect(badge.querySelector("span[aria-hidden]")).toBeInTheDocument();
    expect(badge.getAttribute("title")).toContain(
      "Known Exploited Vulnerabilities",
    );
  });

  it("renders nothing for kev=false", () => {
    const { container } = render(<KevBadge kev={false} />);
    expect(screen.queryByTestId("kev-badge")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when kev is undefined (detail schema lag)", () => {
    const { container } = render(<KevBadge kev={undefined} />);
    expect(screen.queryByTestId("kev-badge")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("folds the due date into the tooltip without rendering it inline", () => {
    render(<KevBadge kev={true} dueDate="2026-07-15" />);
    const badge = screen.getByTestId("kev-badge");
    expect(badge.getAttribute("title")).toContain("2026-07-15");
    expect(badge).toHaveAttribute("data-kev-due-date", "2026-07-15");
    expect(
      screen.queryByTestId("kev-badge-due-date"),
    ).not.toBeInTheDocument();
  });

  it("renders the due date inline when showDueDate is set (drawer)", () => {
    render(<KevBadge kev={true} dueDate="2026-07-15" showDueDate />);
    const due = screen.getByTestId("kev-badge-due-date");
    expect(due.textContent).toContain("2026-07-15");
  });

  it("omits the inline due date when the catalog entry has none", () => {
    render(<KevBadge kev={true} dueDate={null} showDueDate />);
    expect(screen.getByTestId("kev-badge")).toBeInTheDocument();
    expect(
      screen.queryByTestId("kev-badge-due-date"),
    ).not.toBeInTheDocument();
  });
});
