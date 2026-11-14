/**
 * PrimerRoot and the Primer package under Vitest.
 *
 * Proves two things: Vitest can load @primer/react at all (it needs
 * `server.deps.inline` in vite.config.ts), and the provider's colour mode
 * follows the `dark` class on <html>, which is the only place our module-level
 * theme state is visible.
 */
import { Button, useTheme } from "@primer/react";
import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import PrimerRoot, { primerColorMode } from "../../../src/components/primer/PrimerRoot";

function Probe() {
  const { colorMode } = useTheme();
  return <span data-testid="mode">{colorMode}</span>;
}

afterEach(() => {
  document.documentElement.classList.remove("dark");
});

describe("PrimerRoot", () => {
  it("renders a Primer Button (the package loads under Vitest)", () => {
    render(
      <PrimerRoot>
        <Button variant="primary">Save</Button>
      </PrimerRoot>,
    );
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();
  });

  it("maps our two themes to Primer's day and night", () => {
    expect(primerColorMode("light")).toBe("day");
    expect(primerColorMode("dark")).toBe("night");
  });

  it("starts from the class already on <html>", () => {
    document.documentElement.classList.add("dark");
    render(
      <PrimerRoot>
        <Probe />
      </PrimerRoot>,
    );
    expect(screen.getByTestId("mode").textContent).toBe("night");
  });

  it("follows the class when something flips it later", async () => {
    render(
      <PrimerRoot>
        <Probe />
      </PrimerRoot>,
    );
    expect(screen.getByTestId("mode").textContent).toBe("day");
    await act(async () => {
      document.documentElement.classList.add("dark");
      await Promise.resolve();
    });
    expect(screen.getByTestId("mode").textContent).toBe("night");
    await act(async () => {
      document.documentElement.classList.remove("dark");
      await Promise.resolve();
    });
    expect(screen.getByTestId("mode").textContent).toBe("day");
  });

  it("adds no wrapper element and no global style (contextOnly, no BaseStyles)", () => {
    const { container } = render(
      <PrimerRoot>
        <p>child</p>
      </PrimerRoot>,
    );
    expect(container.firstElementChild?.tagName).toBe("P");
  });
});
