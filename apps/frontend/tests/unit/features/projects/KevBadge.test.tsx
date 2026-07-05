/**
 * KevBadge — unit tests (CISA KEV signal + C3 SLA states).
 *
 * Validates the render contract:
 *   - kev=true          → "KEV" badge with a colored dot + text label.
 *   - kev=false / undef → nothing (absence reads as "not listed").
 *   - dueDate           → folded into the title tooltip; rendered inline only
 *                         when `showDueDate` (drawer surface).
 *   - SLA states (C3)   → data-due-state anchor + per-state tone/text:
 *       overdue  → solid critical fill + "Overdue by n days"
 *       imminent → amber tint + "Due in n days" / "Due today"
 *       ok       → pre-C3 appearance + muted "Due {date}"
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { KevBadge } from "@/features/projects/components/KevBadge";

// Injectable clock — local 2026-07-03 (mirrors dueDate.test.ts) so the SLA
// classification never depends on the machine's wall clock.
const NOW = new Date(2026, 6, 3, 10, 30, 0);

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
    // No due date → no SLA state anchor.
    expect(badge).not.toHaveAttribute("data-due-state");
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
    render(<KevBadge kev={true} dueDate="2026-07-15" now={NOW} />);
    const badge = screen.getByTestId("kev-badge");
    expect(badge.getAttribute("title")).toContain("2026-07-15");
    expect(badge).toHaveAttribute("data-kev-due-date", "2026-07-15");
    expect(
      screen.queryByTestId("kev-badge-due-date"),
    ).not.toBeInTheDocument();
  });

  it("renders the due date inline when showDueDate is set (drawer)", () => {
    render(
      <KevBadge kev={true} dueDate="2026-08-15" showDueDate now={NOW} />,
    );
    const due = screen.getByTestId("kev-badge-due-date");
    expect(due.textContent).toContain("2026-08-15");
  });

  it("omits the inline due date when the catalog entry has none", () => {
    render(<KevBadge kev={true} dueDate={null} showDueDate now={NOW} />);
    expect(screen.getByTestId("kev-badge")).toBeInTheDocument();
    expect(
      screen.queryByTestId("kev-badge-due-date"),
    ).not.toBeInTheDocument();
  });

  it("renders the overdue state: solid critical tone + 'Overdue by n days'", () => {
    render(
      <KevBadge kev={true} dueDate="2026-06-30" showDueDate now={NOW} />,
    );
    const badge = screen.getByTestId("kev-badge");
    expect(badge).toHaveAttribute("data-due-state", "overdue");
    // Escalated solid fill (tailwind-merge drops the /10 tint for the solid).
    expect(badge.className).toContain("bg-risk-critical");
    expect(badge.className).not.toContain("bg-risk-critical/10");
    // Color + text pairing — the deadline breach is spelled out.
    expect(
      screen.getByTestId("kev-badge-due-date").textContent,
    ).toBe("Overdue by 3 days");
  });

  it("renders the imminent state: amber tone + 'Due in n days'", () => {
    render(
      <KevBadge kev={true} dueDate="2026-07-08" showDueDate now={NOW} />,
    );
    const badge = screen.getByTestId("kev-badge");
    expect(badge).toHaveAttribute("data-due-state", "imminent");
    expect(badge.className).toMatch(/risk-medium|yellow/);
    expect(
      screen.getByTestId("kev-badge-due-date").textContent,
    ).toBe("Due in 5 days");
  });

  it("renders 'Due today' when the deadline is today (imminent, days=0)", () => {
    render(
      <KevBadge kev={true} dueDate="2026-07-03" showDueDate now={NOW} />,
    );
    const badge = screen.getByTestId("kev-badge");
    expect(badge).toHaveAttribute("data-due-state", "imminent");
    expect(
      screen.getByTestId("kev-badge-due-date").textContent,
    ).toBe("Due today");
  });

  it("renders the ok state unchanged: critical tint + muted 'Due {date}'", () => {
    render(
      <KevBadge kev={true} dueDate="2026-08-15" showDueDate now={NOW} />,
    );
    const badge = screen.getByTestId("kev-badge");
    expect(badge).toHaveAttribute("data-due-state", "ok");
    // Pre-C3 tint retained, no escalation classes.
    expect(badge.className).toContain("bg-risk-critical/10");
    expect(badge.className).not.toMatch(/risk-medium|yellow/);
    expect(
      screen.getByTestId("kev-badge-due-date").textContent,
    ).toBe("Due 2026-08-15");
  });

  it("carries the state anchor on compact list rows too (no inline text)", () => {
    // List rows pass showDueDate={false} — the state rides on the badge tone
    // and the data-due-state anchor while the row stays quiet.
    render(<KevBadge kev={true} dueDate="2026-06-30" now={NOW} />);
    const badge = screen.getByTestId("kev-badge");
    expect(badge).toHaveAttribute("data-due-state", "overdue");
    expect(
      screen.queryByTestId("kev-badge-due-date"),
    ).not.toBeInTheDocument();
  });

  it("degrades a malformed due date to the pre-C3 badge (no SLA state)", () => {
    render(
      <KevBadge kev={true} dueDate="not-a-date" showDueDate now={NOW} />,
    );
    const badge = screen.getByTestId("kev-badge");
    expect(badge).not.toHaveAttribute("data-due-state");
    expect(
      screen.getByTestId("kev-badge-due-date").textContent,
    ).toContain("not-a-date");
  });
});
