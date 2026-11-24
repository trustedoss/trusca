import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusPill } from "@/components/primer/status-pill";

describe("StatusPill", () => {
  it("renders the label with a decorative dot and the tone's classes", () => {
    render(<StatusPill tone="warning">Pending</StatusPill>);
    const pill = screen.getByText("Pending").parentElement as HTMLElement;
    expect(pill).toHaveAttribute("data-tone", "warning");
    expect(pill.className).toContain("bg-status-warning-subtle");
    expect(pill.className).not.toContain("risk-medium");
    expect(pill.querySelector('span[aria-hidden="true"]')).not.toBeNull();
  });

  it("lets a caller override data-tone", () => {
    render(
      <StatusPill tone="success" data-tone="done">
        Done
      </StatusPill>,
    );
    expect(screen.getByText("Done").parentElement).toHaveAttribute("data-tone", "done");
  });
});
