/**
 * PageHeader — unit tests for W12-A.
 *
 * Coverage:
 *   - stacked variant renders an H1 title + muted subtitle, taller `py-4` chrome.
 *   - stacked omits the subtitle when no `description` is supplied.
 *   - bar variant renders the 48 px slim row (no subtitle) with the right slot.
 *   - The `actions` slot renders arbitrary nodes (buttons or meta), preserving
 *     caller-owned `data-testid`s so existing harness selectors survive.
 *   - `data-testid` is forwarded to the <header> root, and `titleProps` to the H1.
 *   - Chrome is unified to `bg-background` + `border-b` across both variants.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "@/components/PageHeader";

describe("PageHeader", () => {
  it("renders an H1 title and muted subtitle in the stacked variant", () => {
    render(
      <PageHeader
        title="Scans"
        description="Global scan queue"
        data-testid="hdr"
      />,
    );
    const root = screen.getByTestId("hdr");
    expect(root.tagName).toBe("HEADER");
    expect(root.className).toContain("bg-background");
    expect(root.className).toContain("border-b");
    expect(root.className).toContain("py-4");

    const title = screen.getByRole("heading", { level: 1 });
    expect(title).toHaveTextContent("Scans");
    expect(screen.getByText("Global scan queue").className).toContain(
      "text-muted-foreground",
    );
  });

  it("omits the subtitle when no description is supplied", () => {
    render(<PageHeader title="Users" data-testid="hdr2" />);
    expect(screen.getByTestId("hdr2").querySelector("p")).toBeNull();
  });

  it("renders the slim 48 px bar variant with an actions slot", () => {
    render(
      <PageHeader
        variant="bar"
        title="Projects"
        data-testid="hdr3"
        actions={<button data-testid="register">Register</button>}
      />,
    );
    const root = screen.getByTestId("hdr3");
    expect(root.getAttribute("style")).toContain("var(--layout-header)");
    // bar variant never renders a subtitle paragraph.
    expect(root.querySelector("p")).toBeNull();
    expect(screen.getByTestId("register")).toBeInTheDocument();
  });

  it("does not render a description in the bar variant even if passed", () => {
    render(
      <PageHeader
        variant="bar"
        title="Dashboard"
        description="ignored in bar"
        data-testid="hdr4"
      />,
    );
    expect(
      screen.queryByText("ignored in bar"),
    ).not.toBeInTheDocument();
  });

  it("renders a meta block under the description in the stacked variant", () => {
    render(
      <PageHeader
        title="Health"
        description="System status"
        meta={<span data-testid="updated-at">updated 2m ago</span>}
        data-testid="hdr-meta"
      />,
    );
    expect(screen.getByTestId("updated-at")).toHaveTextContent("updated 2m ago");
  });

  it("forwards titleProps onto the H1 (e.g. a harness test id)", () => {
    render(
      <PageHeader
        title="Health"
        titleProps={{ "data-testid": "page-title" }}
      />,
    );
    const title = screen.getByTestId("page-title");
    expect(title.tagName).toBe("H1");
    expect(title).toHaveTextContent("Health");
  });
});
