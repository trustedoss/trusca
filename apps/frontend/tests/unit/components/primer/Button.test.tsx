/**
 * The Button wrapper keeps the shadcn vocabulary on top of Primer's Button.
 * Screens depend on three things: the variant names map to the right Primer
 * look, `asChild` turns a router Link into a real link that still looks like a
 * button, and the child's own attributes survive.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Link } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { Button } from "@/components/primer/Button";

const dataVariant = (el: HTMLElement) =>
  el.getAttribute("data-variant") ?? el.closest("[data-variant]")?.getAttribute("data-variant");

describe("Button", () => {
  it.each([
    ["default", "primary"],
    ["destructive", "danger"],
    ["outline", "default"],
    ["ghost", "invisible"],
    ["link", "link"],
  ] as const)("maps variant %s to Primer's %s", (variant, primer) => {
    render(<Button variant={variant}>Go</Button>);
    expect(dataVariant(screen.getByRole("button", { name: "Go" }))).toBe(primer);
  });

  it("runs onClick and respects disabled", async () => {
    const onClick = vi.fn();
    render(
      <>
        <Button onClick={onClick}>On</Button>
        <Button disabled onClick={onClick}>Off</Button>
      </>,
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "On" }));
    await user.click(screen.getByRole("button", { name: "Off" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("asChild renders the child link with the button look and the child's props", () => {
    render(
      <MemoryRouter>
        <Button asChild variant="outline" data-testid="cta">
          <Link to="/projects/new" className="extra" aria-label="Create">
            New project
          </Link>
        </Button>
      </MemoryRouter>,
    );
    const link = screen.getByTestId("cta");
    expect(link.tagName).toBe("A");
    expect(link).toHaveAttribute("href", "/projects/new");
    expect(link).toHaveAttribute("aria-label", "Create");
    expect(link.className).toContain("extra");
    expect(link).toHaveTextContent("New project");
  });

  it("size icon adds the square-button class", () => {
    render(<Button size="icon" aria-label="Close">x</Button>);
    expect(screen.getByRole("button", { name: "Close" }).className).toContain("primer-btn-icon");
  });
});
