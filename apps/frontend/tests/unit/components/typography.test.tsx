/**
 * Typography primitives — unit tests for W12-A.
 *
 * Coverage:
 *   - Each named component renders the correct element (h1/h2/p/span).
 *   - The canonical scale classes are applied (page title 18 px, subtitle muted,
 *     eyebrow uppercase, etc.) so the documented design-system scale is enforced
 *     in one place instead of drifting per page.
 *   - `Body` toggles muted foreground via the `muted` prop.
 *   - className overrides merge onto the rendered element.
 *   - `textVariants` exposes the variant → class map.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  Body,
  Caption,
  Eyebrow,
  PageTitle,
  SectionTitle,
  Subtitle,
  textVariants,
} from "@/components/ui/typography";

describe("Typography", () => {
  it("renders PageTitle as an h1 with the 18 px semibold scale", () => {
    render(<PageTitle data-testid="t">Scans</PageTitle>);
    const el = screen.getByTestId("t");
    expect(el.tagName).toBe("H1");
    expect(el.className).toContain("text-lg");
    expect(el.className).toContain("font-semibold");
    expect(el.className).toContain("tracking-tight");
  });

  it("renders SectionTitle as an h2 at the 16 px scale", () => {
    render(<SectionTitle data-testid="s">Recent scans</SectionTitle>);
    const el = screen.getByTestId("s");
    expect(el.tagName).toBe("H2");
    expect(el.className).toContain("text-base");
    expect(el.className).toContain("font-semibold");
  });

  it("renders Subtitle as a muted paragraph", () => {
    render(<Subtitle data-testid="sub">Queue status</Subtitle>);
    const el = screen.getByTestId("sub");
    expect(el.tagName).toBe("P");
    expect(el.className).toContain("text-sm");
    expect(el.className).toContain("text-muted-foreground");
  });

  it("toggles Body muted foreground via the muted prop", () => {
    const { rerender } = render(<Body data-testid="b">hello</Body>);
    expect(screen.getByTestId("b").className).toContain("text-foreground");

    rerender(
      <Body data-testid="b" muted>
        hello
      </Body>,
    );
    expect(screen.getByTestId("b").className).toContain("text-muted-foreground");
  });

  it("renders Caption and Eyebrow as spans with their scale", () => {
    render(
      <>
        <Caption data-testid="cap">2h ago</Caption>
        <Eyebrow data-testid="eye">Severity</Eyebrow>
      </>,
    );
    const cap = screen.getByTestId("cap");
    const eye = screen.getByTestId("eye");
    expect(cap.tagName).toBe("SPAN");
    expect(cap.className).toContain("text-xs");
    expect(eye.className).toContain("uppercase");
    expect(eye.className).toContain("tracking-wide");
  });

  it("merges className overrides onto the element", () => {
    render(
      <PageTitle data-testid="o" className="custom-flag">
        x
      </PageTitle>,
    );
    expect(screen.getByTestId("o").className).toContain("custom-flag");
  });

  it("exposes the variant → class map via textVariants", () => {
    expect(textVariants({ variant: "pageTitle" })).toContain("text-lg");
    expect(textVariants({ variant: "eyebrow" })).toContain("uppercase");
    // default variant is body
    expect(textVariants()).toContain("text-sm");
  });
});
