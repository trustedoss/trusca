/**
 * SlaBadge — unit tests (X1 SLA/aging).
 *
 * Validates the render contract:
 *   - status=null/undefined → nothing (no SLA for this severity).
 *   - each of the three server states → dot + literal state label (color is
 *     never the only signal) + `data-sla-status` anchor.
 *   - overdue → solid critical fill; imminent → amber tone; ok → neutral
 *     low-emphasis tone.
 *   - dueDate → tooltip always; inline only when `showDueDate` (drawer).
 *   - the state is the SERVER value verbatim — no client recomputation, so a
 *     stale-looking date never flips the badge away from what the server's
 *     `?sla=` filter matched.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SlaBadge } from "@/features/projects/components/SlaBadge";

describe("SlaBadge", () => {
  it("renders nothing when status is null (no SLA for this severity)", () => {
    const { container } = render(
      <SlaBadge status={null} dueDate="2026-07-01T00:00:00Z" />,
    );
    expect(screen.queryByTestId("sla-badge")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when status is undefined (schema lag)", () => {
    const { container } = render(<SlaBadge status={undefined} />);
    expect(screen.queryByTestId("sla-badge")).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the overdue state: solid critical fill + literal label + dot", () => {
    render(<SlaBadge status="overdue" dueDate="2026-06-30T00:00:00Z" />);
    const badge = screen.getByTestId("sla-badge");
    expect(badge).toHaveAttribute("data-sla-status", "overdue");
    // Escalated solid fill (tailwind-merge drops the /10 tint for the solid).
    expect(badge.className).toContain("bg-risk-critical");
    expect(badge.className).not.toContain("bg-risk-critical/10");
    // Color + text pairing — never color-only.
    expect(badge.textContent).toContain("Overdue");
    expect(badge.querySelector("span[aria-hidden]")).toBeInTheDocument();
  });

  it("renders the imminent state with the amber tone", () => {
    render(<SlaBadge status="imminent" dueDate="2026-07-05T00:00:00Z" />);
    const badge = screen.getByTestId("sla-badge");
    expect(badge).toHaveAttribute("data-sla-status", "imminent");
    expect(badge.className).toMatch(/risk-medium|yellow/);
    expect(badge.textContent).toContain("Due soon");
  });

  it("renders the ok state with the neutral low-emphasis tone", () => {
    render(<SlaBadge status="ok" dueDate="2026-12-01T00:00:00Z" />);
    const badge = screen.getByTestId("sla-badge");
    expect(badge).toHaveAttribute("data-sla-status", "ok");
    expect(badge.className).toMatch(/risk-info|slate/);
    expect(badge.className).not.toMatch(/risk-critical|risk-medium/);
    expect(badge.textContent).toContain("On track");
  });

  it("folds the due DATE into the tooltip without rendering it inline", () => {
    render(<SlaBadge status="ok" dueDate="2026-12-01T00:00:00Z" />);
    const badge = screen.getByTestId("sla-badge");
    // Date-granularity display: the time part of the ISO instant is dropped.
    expect(badge.getAttribute("title")).toContain("2026-12-01");
    expect(badge).toHaveAttribute("data-sla-due-date", "2026-12-01");
    expect(screen.queryByTestId("sla-badge-due-date")).not.toBeInTheDocument();
  });

  it("renders the due date inline when showDueDate is set (drawer surface)", () => {
    render(
      <SlaBadge status="imminent" dueDate="2026-07-05T12:34:56Z" showDueDate />,
    );
    const due = screen.getByTestId("sla-badge-due-date");
    expect(due.textContent).toContain("2026-07-05");
    // Never the raw time — deadlines are communicated at date granularity.
    expect(due.textContent).not.toContain("12:34");
  });

  it("omits the inline due date when the server sent none", () => {
    render(<SlaBadge status="ok" dueDate={null} showDueDate />);
    expect(screen.getByTestId("sla-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("sla-badge-due-date")).not.toBeInTheDocument();
  });

  it("renders the server state verbatim — no client-side recomputation", () => {
    // A due date far in the FUTURE paired with server-status "overdue": the
    // badge must trust the server (operator-tuned windows / DB clock), so it
    // stays "overdue" instead of locally reclassifying to "ok".
    render(<SlaBadge status="overdue" dueDate="2099-01-01T00:00:00Z" />);
    const badge = screen.getByTestId("sla-badge");
    expect(badge).toHaveAttribute("data-sla-status", "overdue");
    expect(badge.textContent).toContain("Overdue");
  });
});
