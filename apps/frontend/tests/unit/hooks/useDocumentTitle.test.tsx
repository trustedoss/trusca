/**
 * useDocumentTitle: unit tests.
 *
 * The coverage contract proves every screen has a title mechanism; this proves
 * the mechanism produces the right string. Both are needed: a screen could
 * call the hook with segments that are all empty and still pass the contract.
 */
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "@/components/PageHeader";
import { documentTitle, useDocumentTitle } from "@/hooks/useDocumentTitle";

function Screen({ segments }: { segments: (string | null | undefined)[] }) {
  useDocumentTitle(...segments);
  return null;
}

describe("documentTitle", () => {
  it("joins segments most-specific-first and ends with the brand", () => {
    expect(documentTitle("CVE-2024-1234", "payments-api")).toBe(
      "CVE-2024-1234 · payments-api · TRUSCA",
    );
  });

  it("drops segments that have not loaded yet", () => {
    // A record name arrives a tick after the screen mounts. Without this the
    // tab would read " ·  · TRUSCA" for that tick.
    expect(documentTitle(null, undefined, "")).toBe("TRUSCA");
    expect(documentTitle("Scans", null)).toBe("Scans · TRUSCA");
  });

  it("treats a whitespace-only segment as absent", () => {
    expect(documentTitle("   ", "Projects")).toBe("Projects · TRUSCA");
  });

  it("does not say the brand twice", () => {
    // The login heading is "Sign in to TRUSCA"; appending the brand to that
    // reads like a bug.
    expect(documentTitle("Sign in to TRUSCA")).toBe("Sign in to TRUSCA");
    expect(documentTitle("Overview", "TRUSCA demo")).toBe(
      "Overview · TRUSCA demo",
    );
  });
});

describe("useDocumentTitle", () => {
  it("sets the tab title while mounted", () => {
    render(<Screen segments={["Approvals"]} />);
    expect(document.title).toBe("Approvals · TRUSCA");
  });

  it("follows the title when the record name arrives", () => {
    const { rerender } = render(<Screen segments={[null, "payments-api"]} />);
    expect(document.title).toBe("payments-api · TRUSCA");

    rerender(<Screen segments={["django 4.2", "payments-api"]} />);
    expect(document.title).toBe("django 4.2 · payments-api · TRUSCA");
  });

  it("leaves the tab alone while it has nothing to say", () => {
    // A detail screen mounts before its record loads. Writing the bare brand
    // here would flash "TRUSCA", the exact state this hook exists to end.
    document.title = "previous · TRUSCA";
    render(<Screen segments={[null, undefined]} />);
    expect(document.title).toBe("previous · TRUSCA");
  });
});

describe("PageHeader", () => {
  it("names the tab from a string title without being asked", () => {
    render(<PageHeader title="Policies" />);
    expect(document.title).toBe("Policies · TRUSCA");
  });

  it("takes documentTitle when the heading is markup", () => {
    render(
      <PageHeader
        title={<span>Policies</span>}
        documentTitle="Policies"
      />,
    );
    expect(document.title).toBe("Policies · TRUSCA");
  });

  it("leaves the tab alone when the title is markup and nothing is given", () => {
    document.title = "untouched";
    render(<PageHeader title={<span>Policies</span>} />);
    expect(document.title).toBe("untouched");
  });

  it("opts out on an explicit null", () => {
    document.title = "untouched";
    render(<PageHeader title="Policies" documentTitle={null} />);
    expect(document.title).toBe("untouched");
  });
});
