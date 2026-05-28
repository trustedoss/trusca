/**
 * EmptyState — unit tests for W11-G.
 *
 * Coverage:
 *   - Renders the icon, title, and description.
 *   - Omits the action region when no `action` prop is provided.
 *   - Forwards className overrides onto the root container.
 *   - Sets `role="status"` so screen readers announce the empty state
 *     without each call site having to plumb aria-live attributes.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/EmptyState";

describe("EmptyState", () => {
  it("renders the icon, title, and description", () => {
    render(
      <EmptyState
        icon={<svg data-testid="es-icon" />}
        title="No projects yet"
        description="Register your first repository to start scanning."
        data-testid="es"
      />,
    );

    expect(screen.getByTestId("es-icon")).toBeInTheDocument();
    expect(screen.getByText("No projects yet")).toBeInTheDocument();
    expect(
      screen.getByText("Register your first repository to start scanning."),
    ).toBeInTheDocument();
  });

  it("omits the action slot when no action prop is provided", () => {
    render(
      <EmptyState
        icon={<svg />}
        title="No notifications"
        data-testid="es-no-action"
      />,
    );

    // The action <div className="mt-2"> only renders when `action` is truthy.
    const root = screen.getByTestId("es-no-action");
    // The root has exactly 2 children when description is absent (icon circle + title)
    // or 3 when description is present. No action div should be present.
    expect(root.querySelector(".mt-2")).toBeNull();
  });

  it("renders an action node when supplied", () => {
    render(
      <EmptyState
        icon={<svg />}
        title="No projects yet"
        action={<button data-testid="es-cta">Register project</button>}
      />,
    );

    expect(screen.getByTestId("es-cta")).toBeInTheDocument();
  });

  it("forwards className overrides onto the root container", () => {
    render(
      <EmptyState
        icon={<svg />}
        title="No data"
        className="m-6 custom-flag"
        data-testid="es-class"
      />,
    );

    const root = screen.getByTestId("es-class");
    expect(root.className).toContain("custom-flag");
    expect(root.className).toContain("m-6");
  });

  it("sets role=status for assistive-tech announcement", () => {
    render(<EmptyState icon={<svg />} title="No scans yet" />);

    // Title text is rendered, and the surrounding container exposes a status
    // role so screen readers announce the empty state.
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("No scans yet");
  });
});
