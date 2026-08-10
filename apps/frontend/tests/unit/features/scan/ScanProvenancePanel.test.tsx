/**
 * ScanProvenancePanel — unit tests (gap #31).
 *
 * The panel is pure presentational, so it renders directly with a
 * `ScanProvenanceRead` fixture. What is asserted is the reading a user takes
 * away: an absent record renders nothing rather than an empty card, a tree that
 * carried no manifests says so in words (which is a finding, not a blank), and
 * a truncated listing admits it — a list that stops silently reads as complete.
 *
 * The EN/KO key parity check lives here too: both locales serve this panel, and
 * a key added to one only shows up as a raw `provenance.…` string on screen in
 * the other.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScanProvenancePanel } from "@/features/scan/ScanProvenancePanel";
import type { ScanProvenanceRead } from "@/lib/projectsApi";
import enScans from "@/locales/en/scans.json";
import koScans from "@/locales/ko/scans.json";

function provenance(
  overrides: Partial<ScanProvenanceRead> = {},
): ScanProvenanceRead {
  return {
    scan_id: "11111111-2222-3333-4444-555555555555",
    kind: "source",
    manifests: null,
    document: null,
    ...overrides,
  };
}

describe("ScanProvenancePanel", () => {
  it("renders nothing when neither half was recorded", () => {
    // Every scan from before the feature is this case. An empty card on each
    // of them would be noise, not information.
    const { container } = render(
      <ScanProvenancePanel provenance={provenance()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing while the query has no data", () => {
    const { container } = render(<ScanProvenancePanel provenance={undefined} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("lists the manifests the scanned tree carried", () => {
    render(
      <ScanProvenancePanel
        provenance={provenance({
          manifests: {
            files: [
              { path: "package.json", size: 1240, sha256: "a".repeat(64) },
              { path: "services/api/go.mod", size: 512, sha256: null },
            ],
            count: 2,
            truncated: false,
          },
        })}
      />,
    );

    expect(screen.getByText("package.json")).toBeInTheDocument();
    expect(screen.getByText("services/api/go.mod")).toBeInTheDocument();
    expect(screen.getByText("1.2 KB")).toBeInTheDocument();
  });

  it("says so in words when the tree carried no manifest", () => {
    // count 0 is a measurement — the scan looked and found none — and is a
    // likely answer to "why is this component missing?". It must not render as
    // an empty list the reader has to interpret.
    render(
      <ScanProvenancePanel
        provenance={provenance({
          manifests: { files: [], count: 0, truncated: false },
        })}
      />,
    );

    expect(
      screen.getByText(/carried no dependency manifest/i),
    ).toBeInTheDocument();
  });

  it("admits when the listing was cut short", () => {
    render(
      <ScanProvenancePanel
        provenance={provenance({
          manifests: {
            files: [{ path: "package.json", size: 10, sha256: null }],
            count: 1,
            truncated: true,
          },
        })}
      />,
    );

    expect(screen.getByText(/the tree carried more/i)).toBeInTheDocument();
  });

  it("shows what an uploaded document claimed, and says they are claims", () => {
    render(
      <ScanProvenancePanel
        provenance={provenance({
          kind: "sbom",
          document: {
            format: "cyclonedx",
            spec_version: "1.6",
            serial_number: "urn:uuid:1111",
            subject: "supplier-app",
            subject_version: "4.2.0",
            created: "2026-08-01T10:00:00Z",
            tools: [{ name: "cdxgen", version: "12.3.3" }],
            authors: ["Release Engineering"],
            supplier: "Example Corp",
            component_count: 412,
            byte_size: 90210,
            original_filename: "supplier.cdx.json",
          },
        })}
      />,
    );

    expect(screen.getByText("cyclonedx 1.6")).toBeInTheDocument();
    expect(screen.getByText("cdxgen 12.3.3")).toBeInTheDocument();
    expect(screen.getByText("supplier-app 4.2.0")).toBeInTheDocument();
    expect(screen.getByText("Example Corp")).toBeInTheDocument();
    expect(screen.getByText("412")).toBeInTheDocument();
    // The reader has to know these were not verified by us.
    expect(screen.getByText(/document's own claim/i)).toBeInTheDocument();
  });

  it("omits fields the document did not state", () => {
    // A blank row invites the reading "the supplier is empty" where the truth
    // is "the document named no supplier".
    render(
      <ScanProvenancePanel
        provenance={provenance({
          kind: "sbom",
          document: {
            format: "spdx-json",
            spec_version: null,
            serial_number: null,
            subject: null,
            subject_version: null,
            created: null,
            tools: [],
            authors: [],
            supplier: null,
            component_count: 3,
            byte_size: null,
            original_filename: null,
          },
        })}
      />,
    );

    expect(screen.getByText("spdx-json")).toBeInTheDocument();
    expect(screen.queryByText(/Supplier/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Generated by/)).not.toBeInTheDocument();
  });
});

describe("provenance locale parity", () => {
  function leafKeys(value: unknown, prefix = ""): string[] {
    if (value === null || typeof value !== "object") return [prefix];
    return Object.entries(value as Record<string, unknown>).flatMap(([k, v]) =>
      leafKeys(v, prefix ? `${prefix}.${k}` : k),
    );
  }

  it("serves the same keys in both locales", () => {
    // The count-bearing heading interpolates rather than using i18next's
    // plural suffixes: Korean has no plural form, so the suffixed keys would
    // differ between the two locales by design — and the repo's i18n drift
    // check resolves `t()` calls statically, where a suffixed key reads as a
    // missing one.
    const en = new Set(
      leafKeys((enScans as Record<string, unknown>).provenance),
    );
    const ko = new Set(
      leafKeys((koScans as Record<string, unknown>).provenance),
    );

    expect([...ko].sort()).toEqual([...en].sort());
  });
});
