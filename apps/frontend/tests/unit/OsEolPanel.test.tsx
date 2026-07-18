import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OsEolPanel, readOsBlock } from "@/features/scan/OsEolPanel";

describe("readOsBlock", () => {
  it("narrows a well-formed OS block", () => {
    expect(
      readOsBlock({ os: { family: "alpine", name: "3.19.9", eosl: true } }),
    ).toEqual({ family: "alpine", name: "3.19.9", eosl: true });
  });

  it("defaults eosl to false and name to undefined", () => {
    expect(readOsBlock({ os: { family: "debian" } })).toEqual({
      family: "debian",
      name: undefined,
      eosl: false,
    });
  });

  it("returns null when os is absent or malformed", () => {
    expect(readOsBlock({})).toBeNull();
    expect(readOsBlock({ os: null as unknown as object })).toBeNull();
    expect(readOsBlock({ os: { family: "" } })).toBeNull();
    expect(readOsBlock({ os: "alpine" as unknown as object })).toBeNull();
  });
});

describe("OsEolPanel", () => {
  it("renders the EOL panel only when the OS is past end-of-life", () => {
    render(
      <OsEolPanel metadata={{ os: { family: "alpine", name: "3.19.9", eosl: true } }} />,
    );
    const panel = screen.getByTestId("scan-detail-os-eol");
    expect(panel).toHaveAttribute("data-os-eosl", "true");
    expect(panel).toHaveAttribute("data-os-family", "alpine");
    // a11y: a literal label, not color alone.
    expect(panel.textContent).toMatch(/end-of-life/i);
    expect(panel.textContent).toContain("alpine 3.19.9");
  });

  it("renders nothing for a supported release", () => {
    const { container } = render(
      <OsEolPanel metadata={{ os: { family: "alpine", name: "3.20.0", eosl: false } }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when there is no OS metadata (source/sbom scans)", () => {
    const { container } = render(<OsEolPanel metadata={{}} />);
    expect(container).toBeEmptyDOMElement();
  });
});
