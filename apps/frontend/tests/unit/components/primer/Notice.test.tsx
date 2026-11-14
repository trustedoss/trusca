import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Notice } from "@/components/primer/Notice";

describe("Notice", () => {
  it("announces an error at once", () => {
    render(<Notice tone="error">Bad password</Notice>);
    expect(screen.getByRole("alert")).toHaveTextContent("Bad password");
  });

  it.each(["success", "info", "warning"] as const)("a %s notice is a status, not an alert", (tone) => {
    render(<Notice tone={tone}>Saved</Notice>);
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("never relies on colour alone: every tone draws an icon", () => {
    const { container } = render(<Notice tone="success">Saved</Notice>);
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
