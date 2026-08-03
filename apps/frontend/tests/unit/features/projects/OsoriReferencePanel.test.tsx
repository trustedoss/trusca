// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * OSORI reference panel — unit tests (S5-B).
 *
 * The behaviour worth pinning is the framing, not the markup. This data is an
 * outside opinion sitting next to a classification the build gate acts on, and
 * the panel has to keep those two legible as different things — including
 * carrying its own attribution, which ODC-By 1.0 requires and which nothing
 * else on the drawer would supply.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { OsoriReference } from "@/features/projects/api/licensesApi";
import { OsoriReferencePanel } from "@/features/projects/components/OsoriReferencePanel";

function reference(overrides: Partial<OsoriReference> = {}): OsoriReference {
  return {
    name: "GNU General Public License v3.0 only",
    notification_required: true,
    source_disclosure: "EXECUTABLE",
    restrictions: ["Provide Installation Information Required (level 4)"],
    source: "OSORI (olis.or.kr), ODC-By 1.0",
    ...overrides,
  };
}

describe("OsoriReferencePanel", () => {
  it("renders nothing when OSORI has no record", () => {
    const { container } = render(
      <OsoriReferencePanel osori={null} hasOwnSummary={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the record carries no usable field", () => {
    // A row that exists but says nothing is worse than no panel: it implies
    // the data was consulted and had an opinion.
    const { container } = render(
      <OsoriReferencePanel
        osori={reference({
          notification_required: null,
          source_disclosure: null,
          restrictions: [],
        })}
        hasOwnSummary={false}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the disclosure reach, the notification duty, and the caution items", () => {
    render(<OsoriReferencePanel osori={reference()} hasOwnSummary={false} />);

    expect(screen.getByTestId("license-drawer-osori")).toBeInTheDocument();
    expect(
      screen.getByTestId("license-drawer-osori-disclosure"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("license-drawer-osori-notification"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("license-drawer-osori-restriction"),
    ).toHaveTextContent("level 4");
  });

  it("always carries its attribution", () => {
    // ODC-By 1.0 is attribution-only, and a reader may see nothing but this
    // panel — so the credit cannot live somewhere else on the page.
    render(<OsoriReferencePanel osori={reference()} hasOwnSummary />);
    expect(screen.getByTestId("license-drawer-osori")).toHaveTextContent(
      "OSORI",
    );
    expect(screen.getByTestId("license-drawer-osori")).toHaveTextContent(
      "ODC-By",
    );
  });

  it("says in words that it is not this deployment's classification", () => {
    render(<OsoriReferencePanel osori={reference()} hasOwnSummary={false} />);
    const panel = screen.getByTestId("license-drawer-osori");
    // Not left to the dashed border to imply.
    expect(panel.textContent ?? "").toMatch(/reference/i);
  });

  it("renders even when our own catalogue already explains the license", () => {
    // Both appear; the panel records which case it is so a later design change
    // can hide it without guessing.
    render(<OsoriReferencePanel osori={reference()} hasOwnSummary />);
    expect(screen.getByTestId("license-drawer-osori")).toHaveAttribute(
      "data-has-own-summary",
      "true",
    );
  });

  it("survives a disclosure value it has never seen", () => {
    // OSORI owns this vocabulary and can extend it; an unmapped value should
    // render as itself rather than blank.
    render(
      <OsoriReferencePanel
        osori={reference({ source_disclosure: "SOMETHING-NEW" })}
        hasOwnSummary={false}
      />,
    );
    expect(
      screen.getByTestId("license-drawer-osori-disclosure"),
    ).toHaveTextContent("SOMETHING-NEW");
  });

  it("omits the notification line when OSORI has no view on it", () => {
    render(
      <OsoriReferencePanel
        osori={reference({ notification_required: null })}
        hasOwnSummary={false}
      />,
    );
    expect(
      screen.queryByTestId("license-drawer-osori-notification"),
    ).not.toBeInTheDocument();
    // …while the rest of the panel still renders.
    expect(
      screen.getByTestId("license-drawer-osori-disclosure"),
    ).toBeInTheDocument();
  });
});
