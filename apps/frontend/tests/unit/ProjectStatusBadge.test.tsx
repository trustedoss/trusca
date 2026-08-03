import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProjectStatusBadge } from "@/features/projects/components/ProjectStatusBadge";

describe("ProjectStatusBadge", () => {
  it("renders idle when status is null", () => {
    render(<ProjectStatusBadge status={null} />);
    expect(
      screen.getByTestId("project-status-idle"),
    ).toBeInTheDocument();
  });

  it("renders queued / running / succeeded / failed visuals", () => {
    const { rerender } = render(<ProjectStatusBadge status="queued" />);
    expect(screen.getByTestId("project-status-queued")).toBeInTheDocument();
    rerender(<ProjectStatusBadge status="running" />);
    expect(screen.getByTestId("project-status-running")).toBeInTheDocument();
    rerender(<ProjectStatusBadge status="succeeded" />);
    expect(screen.getByTestId("project-status-succeeded")).toBeInTheDocument();
    rerender(<ProjectStatusBadge status="failed" />);
    expect(screen.getByTestId("project-status-failed")).toBeInTheDocument();
  });

  it("renders cancelled with a high-tone visual", () => {
    render(<ProjectStatusBadge status="cancelled" />);
    expect(screen.getByTestId("project-status-cancelled")).toBeInTheDocument();
  });

  it("includes a translated label, not just a color (a11y)", () => {
    render(<ProjectStatusBadge status="succeeded" />);
    const badge = screen.getByTestId("project-status-succeeded");
    expect(badge.textContent).toMatch(/Succeeded/i);
  });
});
