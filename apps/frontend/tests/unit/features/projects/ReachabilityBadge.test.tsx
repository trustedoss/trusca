/**
 * ReachabilityBadge — unit tests (v2.3 r2).
 *
 * Validates the tri-state render contract:
 *   - reachable (true)  → loud "Reachable" badge + colored dot.
 *   - unreachable (false) → calm "Not reachable" badge.
 *   - unknown (null)    → omitted in compact (list) mode, shown in the drawer.
 *   - source            → folded into the title tooltip.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ReachabilityBadge,
  reachabilityState,
} from "@/features/projects/components/ReachabilityBadge";

describe("reachabilityState", () => {
  it("maps the wire boolean | null onto a render state", () => {
    expect(reachabilityState(true)).toBe("reachable");
    expect(reachabilityState(false)).toBe("unreachable");
    expect(reachabilityState(null)).toBe("unknown");
  });
});

describe("ReachabilityBadge", () => {
  it("renders a loud Reachable badge with a colored dot for reachable=true", () => {
    render(<ReachabilityBadge reachable={true} />);
    const badge = screen.getByTestId("reachability-badge-reachable");
    expect(badge).toHaveAttribute("data-reachability", "reachable");
    expect(badge.textContent).toContain("Reachable");
    // Color is paired with a label + dot, never color-only (a11y).
    expect(badge.querySelector("span[aria-hidden]")).toBeInTheDocument();
  });

  it("renders a calm Not reachable badge for reachable=false", () => {
    render(<ReachabilityBadge reachable={false} />);
    const badge = screen.getByTestId("reachability-badge-unreachable");
    expect(badge).toHaveAttribute("data-reachability", "unreachable");
    expect(badge.textContent).toContain("Not reachable");
  });

  it("renders nothing for the not-analyzed (null) state in compact mode", () => {
    const { container } = render(<ReachabilityBadge reachable={null} />);
    expect(
      screen.queryByTestId("reachability-badge-unknown"),
    ).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders an explicit Not analyzed chip when compact is false (drawer)", () => {
    render(<ReachabilityBadge reachable={null} compact={false} />);
    const badge = screen.getByTestId("reachability-badge-unknown");
    expect(badge).toHaveAttribute("data-reachability", "unknown");
    expect(badge.textContent).toContain("Not analyzed");
  });

  it("folds the analyzer source into the title tooltip", () => {
    render(<ReachabilityBadge reachable={true} source="govulncheck" />);
    const badge = screen.getByTestId("reachability-badge-reachable");
    expect(badge.getAttribute("title")).toContain("govulncheck");
    expect(badge.getAttribute("title")).toContain("Reachable");
  });

  it("uses the plain state tooltip when no source is supplied", () => {
    render(<ReachabilityBadge reachable={false} />);
    const badge = screen.getByTestId("reachability-badge-unreachable");
    // The unreachable state tooltip explains the dead-code conclusion.
    expect(badge.getAttribute("title")).toContain("not reachable");
  });
});
